"""Tier 1 tests for safeatomic._checksum.

Scope: validate hash computation, sidecar I/O, and verification.

API note: ``verify_checksum`` returns ``bool`` on mismatch (False),
per its docstring; it does NOT raise ChecksumMismatchError. The raising
behaviour is reserved for ``read_atomic(check_checksum=True)`` in
``_io_core``. Tests assert the documented bool contract.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from safeatomic._checksum import (
    ChecksumInfo,
    compute_hash_data,
    compute_hash_file,
    get_checksum_info,
    verify_checksum,
    write_checksum_file,
)
from safeatomic._paths import checksum_path

# ---------------------------------------------------------------------------
# compute_hash_data
# ---------------------------------------------------------------------------


def test_compute_hash_data_matches_hashlib_sha256() -> None:
    payload = b"abc"
    expected = hashlib.sha256(payload).hexdigest()
    assert compute_hash_data(payload, algo="sha256") == expected


def test_compute_hash_data_default_algo_is_sha256() -> None:
    payload = b"hello"
    assert compute_hash_data(payload) == hashlib.sha256(payload).hexdigest()


def test_compute_hash_data_supports_sha512() -> None:
    payload = b"abc"
    expected = hashlib.sha512(payload).hexdigest()
    assert compute_hash_data(payload, algo="sha512") == expected


def test_compute_hash_data_rejects_invalid_algo() -> None:
    # hashlib.new raises ValueError for unknown algorithms; the function
    # propagates rather than wrapping it.
    with pytest.raises(ValueError):
        compute_hash_data(b"x", algo="not-a-real-algo-zzz")


def test_compute_hash_data_returns_lowercase_hex() -> None:
    digest = compute_hash_data(b"abc", algo="sha256")
    assert digest == digest.lower()
    # 64 hex chars for sha256
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_compute_hash_data_empty_bytes() -> None:
    # Edge case: empty input must still produce the canonical digest.
    assert compute_hash_data(b"") == hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------------------
# compute_hash_file
# ---------------------------------------------------------------------------


def test_compute_hash_file_returns_str_for_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "f.bin"
    target.write_bytes(b"abc")
    result = compute_hash_file(target)
    assert isinstance(result, str)
    assert result == hashlib.sha256(b"abc").hexdigest()


def test_compute_hash_file_agrees_with_compute_hash_data(tmp_path: Path) -> None:
    payload = b"the quick brown fox jumps over the lazy dog"
    target = tmp_path / "f.bin"
    target.write_bytes(payload)
    assert compute_hash_file(target) == compute_hash_data(payload)


def test_compute_hash_file_handles_large_file_streaming(tmp_path: Path) -> None:
    # 2 MiB file: bigger than CHECKSUM_CHUNK_SIZE (1 MiB) so the
    # streaming loop runs more than once.
    payload = b"x" * (2 * 1024 * 1024 + 17)
    target = tmp_path / "big.bin"
    target.write_bytes(payload)
    assert compute_hash_file(target) == hashlib.sha256(payload).hexdigest()


def test_compute_hash_file_raises_filenotfound_for_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.bin"
    with pytest.raises(FileNotFoundError):
        compute_hash_file(missing)


def test_compute_hash_file_accepts_str_path(tmp_path: Path) -> None:
    target = tmp_path / "f.bin"
    target.write_bytes(b"abc")
    assert compute_hash_file(str(target)) == compute_hash_file(target)


def test_compute_hash_file_rejects_invalid_algo(tmp_path: Path) -> None:
    target = tmp_path / "f.bin"
    target.write_bytes(b"x")
    with pytest.raises(ValueError):
        compute_hash_file(target, algo="not-a-real-algo-zzz")


# ---------------------------------------------------------------------------
# write_checksum_file
# ---------------------------------------------------------------------------


def test_write_checksum_file_returns_path(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    sidecar = write_checksum_file(target)
    assert isinstance(sidecar, Path)


def test_write_checksum_file_creates_sidecar_at_expected_location(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    sidecar = write_checksum_file(target)
    assert sidecar == checksum_path(target)
    assert sidecar.exists()


def test_write_checksum_file_sidecar_first_line_has_digest_and_basename(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    sidecar = write_checksum_file(target)
    first_line = sidecar.read_text(encoding="ascii").splitlines()[0]
    parts = first_line.split()
    # GNU coreutils format: "<digest>  <basename>"
    assert parts[0] == hashlib.sha256(b"abc").hexdigest()
    assert parts[-1] == "data.bin"


def test_write_checksum_file_raises_filenotfound_for_missing_target(tmp_path: Path) -> None:
    missing = tmp_path / "missing.bin"
    with pytest.raises(FileNotFoundError):
        write_checksum_file(missing)


def test_write_checksum_file_supports_non_default_algo(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    sidecar = write_checksum_file(target, algo="sha512")
    body = sidecar.read_text(encoding="ascii")
    assert "algo=sha512" in body
    # Digest in line 1 must match sha512.
    first_digest = body.splitlines()[0].split()[0]
    assert first_digest == hashlib.sha512(b"abc").hexdigest()


# ---------------------------------------------------------------------------
# get_checksum_info
# ---------------------------------------------------------------------------


def test_get_checksum_info_returns_checksuminfo_after_write(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    write_checksum_file(target)
    info = get_checksum_info(target)
    assert isinstance(info, ChecksumInfo)
    assert info.path == target
    assert info.algo == "sha256"
    assert info.hash == hashlib.sha256(b"abc").hexdigest()
    assert isinstance(info.timestamp, datetime)


def test_get_checksum_info_returns_none_when_sidecar_missing(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    # No sidecar written -> documented contract: return None.
    assert get_checksum_info(target) is None


def test_get_checksum_info_tolerates_missing_metadata_lines(tmp_path: Path) -> None:
    # Documented: parser accepts missing algo / timestamp lines.
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    sidecar = checksum_path(target)
    digest = hashlib.sha256(b"abc").hexdigest()
    # Only the GNU coreutils line; no algo=, no timestamp=.
    sidecar.write_text(f"{digest}  data.bin\n", encoding="ascii")

    info = get_checksum_info(target)
    assert info is not None
    assert info.hash == digest
    # Default algo when missing.
    assert info.algo == "sha256"


def test_checksuminfo_is_frozen(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    write_checksum_file(target)
    info = get_checksum_info(target)
    assert info is not None
    with pytest.raises((AttributeError, Exception)):
        info.algo = "sha512"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# verify_checksum
# ---------------------------------------------------------------------------


def test_verify_checksum_true_when_expected_matches(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    expected = hashlib.sha256(b"abc").hexdigest()
    assert verify_checksum(target, expected=expected) is True


def test_verify_checksum_is_case_insensitive_for_expected(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    expected_upper = hashlib.sha256(b"abc").hexdigest().upper()
    # Documented: expected is case-insensitive.
    assert verify_checksum(target, expected=expected_upper) is True


def test_verify_checksum_false_when_expected_mismatches(tmp_path: Path) -> None:
    # API contract documented in _checksum.verify_checksum: a mismatch is
    # a normal operational outcome and returns False rather than raising.
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    wrong = "0" * 64
    result = verify_checksum(target, expected=wrong)
    assert result is False


def test_verify_checksum_reads_expected_from_sidecar_when_none(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    write_checksum_file(target)
    # No explicit expected -> read from sidecar; data unchanged -> True.
    assert verify_checksum(target) is True


def test_verify_checksum_returns_false_after_data_tampering(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    write_checksum_file(target)
    # Tamper with data.
    target.write_bytes(b"xyz")
    assert verify_checksum(target) is False


def test_verify_checksum_raises_filenotfound_when_data_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.bin"
    with pytest.raises(FileNotFoundError):
        verify_checksum(missing, expected="0" * 64)


def test_verify_checksum_raises_filenotfound_when_sidecar_missing(tmp_path: Path) -> None:
    # expected=None and no sidecar -> documented to raise FileNotFoundError.
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    with pytest.raises(FileNotFoundError):
        verify_checksum(target)


def test_verify_checksum_rejects_invalid_algo(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    with pytest.raises(ValueError):
        verify_checksum(target, expected="0" * 64, algo="not-a-real-algo-zzz")
