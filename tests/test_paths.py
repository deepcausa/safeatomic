"""Tier 1 tests for safeatomic._paths.

Scope: validate the pure-functional sidecar derivation helpers.

Naming note: the spec referred to ``get_lock_path`` / ``get_checksum_path``
/ ``get_tmp_path``. The actual module exports ``lock_path``,
``checksum_path``, ``tmp_path_for`` (no ``get_`` prefix). Tests are
written against the names that exist in source; we do not invent or
rename. Reported in the final summary.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

import safeatomic
from safeatomic import _constants
from safeatomic._paths import (
    checksum_path,
    is_tmp_name,
    lock_path,
    tmp_path_for,
)

PathFn = Callable[[Path | str], Path]

# ---------------------------------------------------------------------------
# Argument coercion: str and Path both accepted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fn", [lock_path, checksum_path, tmp_path_for])
def test_functions_accept_str(fn: PathFn, tmp_path: Path) -> None:
    target = tmp_path / "file.json"
    result = fn(str(target))
    assert isinstance(result, Path)


@pytest.mark.parametrize("fn", [lock_path, checksum_path, tmp_path_for])
def test_functions_accept_path(fn: PathFn, tmp_path: Path) -> None:
    target = tmp_path / "file.json"
    result = fn(target)
    assert isinstance(result, Path)


@pytest.mark.parametrize("fn", [lock_path, checksum_path, tmp_path_for])
def test_str_and_path_inputs_agree_for_deterministic_fns(
    fn: PathFn,
    tmp_path: Path,
) -> None:
    """lock_path and checksum_path are deterministic; tmp_path_for is not.

    For the deterministic functions, passing str or Path must produce the
    same output. We only check that the function is callable both ways
    for tmp_path_for and that both return paths in the same parent.
    """
    target = tmp_path / "file.json"
    via_str = fn(str(target))
    via_path = fn(target)
    if fn is tmp_path_for:
        # tmp_path_for embeds a random token; we only check shape.
        assert via_str.parent == via_path.parent == tmp_path
    else:
        assert via_str == via_path


# ---------------------------------------------------------------------------
# Sidecar paths differ from target
# ---------------------------------------------------------------------------


def test_lock_path_differs_from_target(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    assert lock_path(target) != target


def test_lock_path_is_sibling_of_target(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    assert lock_path(target).parent == target.parent


def test_lock_path_appends_lock_suffix(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    expected = tmp_path / ("state.json" + _constants.LOCK_SUFFIX)
    assert lock_path(target) == expected


def test_checksum_path_differs_from_target(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    assert checksum_path(target) != target


def test_checksum_path_is_sibling_of_target(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    assert checksum_path(target).parent == target.parent


def test_checksum_path_appends_checksum_suffix(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    expected = tmp_path / ("state.json" + _constants.CHECKSUM_SUFFIX)
    assert checksum_path(target) == expected


def test_lock_and_checksum_paths_differ(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    assert lock_path(target) != checksum_path(target)


# ---------------------------------------------------------------------------
# Tmp path: parent and naming
# ---------------------------------------------------------------------------


def test_tmp_path_is_in_target_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "subdir" / "data.bin"
    result = tmp_path_for(target)
    # Same-directory tmp is mandatory: os.replace is only atomic within
    # the same directory (POSIX rename).
    assert result.parent == target.parent


def test_tmp_path_includes_safeatomic_prefix(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    result = tmp_path_for(target)
    assert result.name.startswith(_constants.TMP_PREFIX)


def test_tmp_path_includes_tmp_suffix(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    result = tmp_path_for(target)
    assert result.name.endswith(_constants.TMP_SUFFIX)


def test_tmp_path_is_recognised_by_is_tmp_name(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    result = tmp_path_for(target)
    assert is_tmp_name(result.name)


def test_tmp_path_does_not_collide_across_calls(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    # Many draws to make accidental collision astronomically unlikely.
    names = {tmp_path_for(target).name for _ in range(64)}
    assert len(names) == 64


def test_tmp_path_does_not_leak_target_basename(tmp_path: Path) -> None:
    # Documented invariant in _paths.tmp_path_for: tmp name does not
    # encode the target's basename. This is a property test, not a
    # value pin: we check that the unusual stem we choose does not
    # appear in any generated tmp name.
    unusual = "very_unusual_stem_xyzzy_42"
    target = tmp_path / f"{unusual}.bin"
    for _ in range(8):
        result = tmp_path_for(target)
        assert unusual not in result.name


# ---------------------------------------------------------------------------
# Multi-suffix preservation
# ---------------------------------------------------------------------------


def test_lock_path_preserves_multi_suffix_basename(tmp_path: Path) -> None:
    # 'archive.tar.gz' -> 'archive.tar.gz.lock', not 'archive.tar.lock'.
    target = tmp_path / "archive.tar.gz"
    result = lock_path(target)
    assert result.name == "archive.tar.gz" + _constants.LOCK_SUFFIX


def test_checksum_path_preserves_multi_suffix_basename(tmp_path: Path) -> None:
    target = tmp_path / "archive.tar.gz"
    result = checksum_path(target)
    assert result.name == "archive.tar.gz" + _constants.CHECKSUM_SUFFIX


# ---------------------------------------------------------------------------
# is_tmp_name behaviour
# ---------------------------------------------------------------------------


def test_is_tmp_name_rejects_unrelated_files() -> None:
    assert not is_tmp_name("state.json")
    assert not is_tmp_name("state.json.lock")
    assert not is_tmp_name("state.json.sha256")
    assert not is_tmp_name("")


def test_is_tmp_name_accepts_canonical_form() -> None:
    canonical = f"{_constants.TMP_PREFIX}abc123{_constants.TMP_SUFFIX}"
    assert is_tmp_name(canonical)


# ---------------------------------------------------------------------------
# No I/O contract: functions must not create files
# ---------------------------------------------------------------------------


def test_path_derivations_perform_no_io(tmp_path: Path) -> None:
    # Target file deliberately does not exist; functions are documented
    # as pure (no I/O). If they tried to stat/create, this would either
    # raise or leave traces. We assert no traces.
    target = tmp_path / "does_not_exist.bin"
    lock_path(target)
    checksum_path(target)
    tmp_path_for(target)
    # Directory should be empty.
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Public-API non-leakage check (informational)
# ---------------------------------------------------------------------------


def test_path_helpers_are_not_exported_publicly() -> None:
    """Spec note: ``open_atomic`` / path helpers must not be public v2 API.

    The package __init__ in v2.0 is intentionally minimal (phase-2 still
    in progress). We assert that the path helpers are NOT exposed via
    ``safeatomic.__all__`` if it exists, and not bound as attributes.
    """
    public_all = getattr(safeatomic, "__all__", ())
    for name in ("lock_path", "checksum_path", "tmp_path_for", "open_atomic"):
        assert name not in public_all
        assert not hasattr(safeatomic, name)
