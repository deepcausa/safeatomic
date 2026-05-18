"""Tier 1 tests for safeatomic._exceptions.

Scope: validate the public exception hierarchy. Names LockTimeoutError
and StaleLockError exist in source as reserved subclasses but are NOT
exported in v2.0; tests treat them as private and do not validate them.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

import pytest

from safeatomic._exceptions import (
    ChecksumMismatchError,
    CrossDeviceAtomicityError,
    LockError,
    SafeAtomicError,
    UnsupportedEnvironmentError,
    UnsupportedEnvironmentWarning,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------


def test_safeatomic_error_subclasses_exception() -> None:
    assert issubclass(SafeAtomicError, Exception)


def test_safeatomic_error_does_not_subclass_os_error() -> None:
    # Documented design choice: safeatomic exceptions are protocol-level,
    # not OS-level. Conflating with OSError would force callers to
    # disambiguate by message. See design/failure-model.md.
    assert not issubclass(SafeAtomicError, OSError)


@pytest.mark.parametrize(
    "exc",
    [
        UnsupportedEnvironmentError,
        ChecksumMismatchError,
        CrossDeviceAtomicityError,
        LockError,
    ],
)
def test_errors_subclass_safeatomic_error(exc: type[BaseException]) -> None:
    assert issubclass(exc, SafeAtomicError)


def test_unsupported_environment_warning_subclasses_user_warning() -> None:
    # Documented in _exceptions.py and design/guarantees-formalization.md §11.
    assert issubclass(UnsupportedEnvironmentWarning, UserWarning)


def test_unsupported_environment_warning_is_not_an_error() -> None:
    # It's a Warning, not an Error. Catching as Exception is allowed
    # (Warning inherits from Exception) but it must not be in the
    # SafeAtomicError hierarchy.
    assert not issubclass(UnsupportedEnvironmentWarning, SafeAtomicError)


# ---------------------------------------------------------------------------
# ChecksumMismatchError: path / expected / actual fields
# ---------------------------------------------------------------------------


def test_checksum_mismatch_error_carries_path_expected_actual(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    err = ChecksumMismatchError(path=target, expected="aaaa", actual="bbbb")
    assert err.path == target
    assert err.expected == "aaaa"
    assert err.actual == "bbbb"


def test_checksum_mismatch_error_str_contains_useful_information(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    err = ChecksumMismatchError(path=target, expected="deadbeef", actual="cafef00d")
    msg = str(err)
    assert "deadbeef" in msg
    assert "cafef00d" in msg
    assert str(target) in msg


def test_checksum_mismatch_error_can_be_raised_and_caught(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    with pytest.raises(ChecksumMismatchError) as exc_info:
        raise ChecksumMismatchError(path=target, expected="a", actual="b")
    assert exc_info.value.expected == "a"
    assert exc_info.value.actual == "b"


def test_checksum_mismatch_error_caught_as_safeatomic_error(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    with pytest.raises(SafeAtomicError):
        raise ChecksumMismatchError(path=target, expected="a", actual="b")


# ---------------------------------------------------------------------------
# CrossDeviceAtomicityError: src / dst fields
# ---------------------------------------------------------------------------


def test_cross_device_error_carries_src_and_dst(tmp_path: Path) -> None:
    src = tmp_path / "from"
    dst = tmp_path / "to"
    err = CrossDeviceAtomicityError(src=src, dst=dst)
    assert err.src == src
    assert err.dst == dst


def test_cross_device_error_str_contains_paths(tmp_path: Path) -> None:
    src = tmp_path / "alpha"
    dst = tmp_path / "beta"
    err = CrossDeviceAtomicityError(src=src, dst=dst)
    msg = str(err)
    assert str(src) in msg
    assert str(dst) in msg


def test_cross_device_error_preserves_cause_via_raise_from(tmp_path: Path) -> None:
    src = tmp_path / "a"
    dst = tmp_path / "b"
    original = OSError("EXDEV (simulated)")
    with pytest.raises(CrossDeviceAtomicityError) as exc_info:
        try:
            raise original
        except OSError as e:
            raise CrossDeviceAtomicityError(src=src, dst=dst) from e
    # Documented: original OSError available via __cause__.
    assert exc_info.value.__cause__ is original


# ---------------------------------------------------------------------------
# Bare exceptions: minimal contract (raisable, no-arg or with-message)
# ---------------------------------------------------------------------------


_UNSUPPORTED_MSG = "filesystem class 'network' not supported in strict mode"
_LOCK_MSG = "lock file unwritable"


def test_unsupported_environment_error_raisable() -> None:
    with pytest.raises(UnsupportedEnvironmentError):
        raise UnsupportedEnvironmentError(_UNSUPPORTED_MSG)


def test_lock_error_raisable() -> None:
    with pytest.raises(LockError):
        raise LockError(_LOCK_MSG)


def test_lock_error_caught_as_safeatomic_error() -> None:
    with pytest.raises(SafeAtomicError):
        raise LockError("x")


# ---------------------------------------------------------------------------
# UnsupportedEnvironmentWarning: can be issued via warnings.warn
# ---------------------------------------------------------------------------


def test_unsupported_environment_warning_can_be_emitted() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn(
            "filesystem class 'network' may not satisfy AtomicVisibility",
            UnsupportedEnvironmentWarning,
            stacklevel=1,
        )
    assert len(caught) == 1
    assert issubclass(caught[0].category, UnsupportedEnvironmentWarning)
    assert issubclass(caught[0].category, UserWarning)


# ---------------------------------------------------------------------------
# repr / str sanity
# ---------------------------------------------------------------------------


def test_repr_of_checksum_mismatch_is_nonempty(tmp_path: Path) -> None:
    err = ChecksumMismatchError(path=tmp_path / "x", expected="a", actual="b")
    assert repr(err)


def test_repr_of_cross_device_error_is_nonempty(tmp_path: Path) -> None:
    err = CrossDeviceAtomicityError(src=tmp_path / "a", dst=tmp_path / "b")
    assert repr(err)
