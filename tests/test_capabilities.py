"""Tier 1 tests for safeatomic._capabilities.

Scope: validate environment detection on a local POSIX-shaped tmp_path.
Tests must be robust on Linux CI containers (overlayfs / tmpfs / ext4
all acceptable). We never assert a specific filesystem type.

Naming note: spec mentioned ``filesystem_class`` value ``local_posix_like``;
the actual module uses ``local_posix_persistent`` and ``local_posix_memory``.
Tests validate the actual literal set. Reported in the final summary.
"""

from __future__ import annotations

import sys
import typing
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

from safeatomic._capabilities import (
    Capability,
    Environment,
    FilesystemClass,
    Platform,
    SymlinkPolicy,
    clear_cache,
    detect_environment,
)

if typing.TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Permitted literal sets, derived from the type aliases (single source of truth)
# ---------------------------------------------------------------------------


PERMITTED_PLATFORMS = set(typing.get_args(Platform))
PERMITTED_FS_CLASSES = set(typing.get_args(FilesystemClass))
PERMITTED_CAPABILITIES = set(typing.get_args(Capability))
PERMITTED_SYMLINK_POLICIES = set(typing.get_args(SymlinkPolicy))


# Spec required these names to be present in the FilesystemClass literal
# (it referenced ``local_posix_like``; the implementation uses
# ``local_posix_persistent``+``local_posix_memory``). We assert the
# implemented vocabulary.
EXPECTED_FS_CLASS_TOKENS = {
    "local_posix_persistent",
    "local_posix_memory",
    "network",
    "windows",
    "object_store",
    "unknown",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache() -> Generator[None, None, None]:
    """Reset the st_dev cache before each test to avoid order coupling."""
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# Type-alias vocabulary
# ---------------------------------------------------------------------------


def test_platform_literal_covers_expected_oses() -> None:
    # The spec lists linux/darwin/freebsd/openbsd/netbsd/windows/unknown.
    for name in ("linux", "darwin", "freebsd", "openbsd", "netbsd", "windows", "unknown"):
        assert name in PERMITTED_PLATFORMS


def test_filesystem_class_literal_covers_expected_tokens() -> None:
    assert EXPECTED_FS_CLASS_TOKENS.issubset(PERMITTED_FS_CLASSES)


def test_capability_uses_tri_state_yes_no_unknown() -> None:
    assert {"yes", "no", "unknown"} == PERMITTED_CAPABILITIES


def test_symlink_policy_includes_unspecified() -> None:
    assert "unspecified" in PERMITTED_SYMLINK_POLICIES


# ---------------------------------------------------------------------------
# detect_environment(): basic shape
# ---------------------------------------------------------------------------


def test_detect_environment_returns_environment(tmp_path: Path) -> None:
    env = detect_environment(tmp_path)
    assert isinstance(env, Environment)


def test_detect_environment_accepts_str_and_path(tmp_path: Path) -> None:
    e_path = detect_environment(tmp_path)
    e_str = detect_environment(str(tmp_path))
    # Same st_dev -> same cached Environment object identity is fine,
    # but we only assert structural equality to be robust.
    assert e_path == e_str


def test_detect_environment_works_on_nonexistent_path(tmp_path: Path) -> None:
    # Documented: path itself need not exist; parent chain walked upward.
    target = tmp_path / "does" / "not" / "exist" / "file.bin"
    env = detect_environment(target)
    assert isinstance(env, Environment)


# ---------------------------------------------------------------------------
# Environment fields: values within permitted vocabularies
# ---------------------------------------------------------------------------


def test_environment_platform_is_permitted_literal(tmp_path: Path) -> None:
    env = detect_environment(tmp_path)
    assert env.platform in PERMITTED_PLATFORMS


def test_environment_filesystem_class_is_permitted_literal(tmp_path: Path) -> None:
    env = detect_environment(tmp_path)
    assert env.filesystem_class in PERMITTED_FS_CLASSES


def test_environment_capability_fields_are_tristate(tmp_path: Path) -> None:
    env = detect_environment(tmp_path)
    for cap in (
        env.supports_fsync_file,
        env.supports_fsync_dir,
        env.supports_atomic_replace,
    ):
        assert cap in PERMITTED_CAPABILITIES


def test_environment_symlink_policy_is_unspecified_in_v2(tmp_path: Path) -> None:
    env = detect_environment(tmp_path)
    # Documented: v2.0 only declares ``unspecified``.
    assert env.symlink_policy == "unspecified"


def test_environment_filesystem_is_str_or_none(tmp_path: Path) -> None:
    env = detect_environment(tmp_path)
    assert env.filesystem is None or isinstance(env.filesystem, str)


# ---------------------------------------------------------------------------
# Required capability flag names exist
# ---------------------------------------------------------------------------


def test_environment_has_supports_fsync_file_and_dir(tmp_path: Path) -> None:
    env = detect_environment(tmp_path)
    # Spec line: supports_fsync_file and supports_fsync_dir must exist
    # (or documented equivalent). NamedTuple attribute access:
    assert hasattr(env, "supports_fsync_file")
    assert hasattr(env, "supports_fsync_dir")


# ---------------------------------------------------------------------------
# Platform plausibility on current host
# ---------------------------------------------------------------------------


def test_environment_platform_matches_sys_platform_family(tmp_path: Path) -> None:
    env = detect_environment(tmp_path)
    sp = sys.platform
    if sp.startswith("linux"):
        assert env.platform == "linux"
    elif sp == "darwin":
        assert env.platform == "darwin"
    elif sp in {"win32", "cygwin"}:
        assert env.platform == "windows"
    # Other platforms (BSDs, unknown) are tolerated; we only pin the
    # mainstream three because CI runs on them.


# ---------------------------------------------------------------------------
# tmp_path classification
# ---------------------------------------------------------------------------


def test_tmp_path_is_not_classified_as_object_store(tmp_path: Path) -> None:
    # CI/container runs may put tmp_path on tmpfs, ext4, overlay, etc.
    # We only forbid the clearly-wrong classifications. object_store is
    # reserved for FUSE-mounted S3/GCS; pytest's tmp_path must never
    # produce that.
    env = detect_environment(tmp_path)
    assert env.filesystem_class != "object_store"


def test_tmp_path_is_not_classified_as_network(tmp_path: Path) -> None:
    # Same rationale: pytest tmp_path is local. NFS/SMB classification
    # would indicate a misdetection.
    env = detect_environment(tmp_path)
    assert env.filesystem_class != "network"


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="windows-class is the right answer on win32",
)
def test_tmp_path_is_not_classified_as_windows_on_posix(tmp_path: Path) -> None:
    env = detect_environment(tmp_path)
    assert env.filesystem_class != "windows"


# ---------------------------------------------------------------------------
# Cache behaviour: repeated calls do not break
# ---------------------------------------------------------------------------


def test_repeated_calls_return_consistent_environment(tmp_path: Path) -> None:
    env1 = detect_environment(tmp_path)
    env2 = detect_environment(tmp_path)
    env3 = detect_environment(tmp_path / "sub" / "file.bin")
    # All three resolve to ancestors on the same st_dev; the cache must
    # return structurally equal envs.
    assert env1 == env2
    # env3 walks up to find an existing ancestor (tmp_path); same st_dev.
    assert env1.filesystem_class == env3.filesystem_class
    assert env1.platform == env3.platform


def test_clear_cache_does_not_change_observable_result(tmp_path: Path) -> None:
    env1 = detect_environment(tmp_path)
    clear_cache()
    env2 = detect_environment(tmp_path)
    # Detection is deterministic for the same mount; values must match
    # before and after the cache flush.
    assert env1 == env2


# ---------------------------------------------------------------------------
# Environment is a NamedTuple (immutable, hashable, equality by value)
# ---------------------------------------------------------------------------


def test_environment_is_immutable(tmp_path: Path) -> None:
    env = detect_environment(tmp_path)
    with pytest.raises(AttributeError):
        env.platform = "linux"  # type: ignore[misc]


def test_environment_is_hashable(tmp_path: Path) -> None:
    env = detect_environment(tmp_path)
    # NamedTuples are hashable when their fields are hashable; all
    # current fields are str | None. Use the hash inside a set so the
    # expression is not flagged as useless.
    assert env in {env}
