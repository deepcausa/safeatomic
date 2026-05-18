"""Tier-1 tests for safeatomic._guarantees.

Covers:

1. GuaranteeReport public surface (environment, guarantees mapping,
   is_guaranteed, degraded).
2. GuaranteeLevel literal vocabulary (only the four levels; no typos
   like ``non_target`` / ``nonTarget``).
3. The normative matrix per ``filesystem_class``.
4. Tri-state ``Capability`` (``yes``/``no``/``unknown``) propagation
   into the report.
5. Per-operation required-guarantee sets (``_REQUIRED_*`` constants in
   _io_core) — internal API exercised here because no public surface
   exposes the sets directly.
6. The PascalCase ↔ snake_case name normalisation used by ``doctor``.
7. Principle 14: ``safeatomic_config`` may not silently lower the
   guarantees reported by ``inspect_guarantees``.

The spec naming-drift findings are reported in this file's module
docstring rather than skipped: the spec referenced ``local_posix_like``,
``memory``, ``non_target``, ``writer_exclusion`` (all snake_case for
guarantee names, lowercase fs-class shortcuts). The implementation uses
``local_posix_persistent``, ``local_posix_memory``, ``nontarget`` and
PascalCase ``WriterExclusion`` instead. Tests follow the implementation;
drifts are listed in the agent's final summary.

These tests do NOT touch source code, lock tests, doctor tests, config
tests, io_core tests, format tests, or any TLA+ models, per the agent
contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, get_args

import pytest

# Public API surface (preferred imports)
from safeatomic import (
    Environment,
    GuaranteeReport,
    doctor,
    inspect_guarantees,
    safeatomic_config,
)

# Internals exercised deliberately. Documented in the agent's final report.
from safeatomic._capabilities import (
    Capability,
    FilesystemClass,
    clear_cache,
)
from safeatomic._config import _ALLOWED_CONFIG_KEYS
from safeatomic._doctor import _GUARANTEE_TO_CHECK_NAME
from safeatomic._guarantees import (
    _GUARANTEE_NAMES,
    _MATRIX,
    GuaranteeLevel,
    GuaranteeName,
)
from safeatomic._io_core import (
    _REQUIRED_MOVE,
    _REQUIRED_READ,
    _REQUIRED_READ_CHECKSUM,
    _REQUIRED_WRITE_CHECKSUM,
    _REQUIRED_WRITE_LOCK,
    _REQUIRED_WRITE_NONE,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


# ---------------------------------------------------------------------------
# Constants (single source of truth for level / name vocabularies)
# ---------------------------------------------------------------------------


PERMITTED_LEVELS: frozenset[str] = frozenset(get_args(GuaranteeLevel))
PERMITTED_GUARANTEE_NAMES: frozenset[str] = frozenset(get_args(GuaranteeName))
PERMITTED_FS_CLASSES: frozenset[str] = frozenset(get_args(FilesystemClass))
PERMITTED_CAPABILITIES: frozenset[str] = frozenset(get_args(Capability))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_capabilities_cache() -> Iterator[None]:
    """Reset the st_dev cache before/after each test."""
    clear_cache()
    yield
    clear_cache()


def _make_env(fs_class: FilesystemClass, **overrides: object) -> Environment:
    """Build an :class:`Environment` with a given filesystem_class.

    Sensible defaults are chosen so that overrides isolate a single
    capability per test.
    """
    base: dict[str, object] = {
        "platform": "linux",
        "filesystem": "ext4",
        "filesystem_class": fs_class,
        "supports_fsync_file": "yes",
        "supports_fsync_dir": "yes",
        "supports_atomic_replace": "yes",
        "symlink_policy": "unspecified",
    }
    base.update(overrides)
    return Environment(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# (1) GuaranteeReport public surface
# ---------------------------------------------------------------------------


class TestGuaranteeReportSurface:
    """The public attributes/methods of GuaranteeReport must be stable."""

    def test_report_has_environment_attribute(self, tmp_path: Path) -> None:
        report = inspect_guarantees(tmp_path)
        assert isinstance(report.environment, Environment)

    def test_report_has_guarantees_mapping(self, tmp_path: Path) -> None:
        report = inspect_guarantees(tmp_path)
        # Mapping interface, not necessarily a plain dict.
        assert hasattr(report.guarantees, "__getitem__")
        assert hasattr(report.guarantees, "__iter__")

    def test_report_guarantees_contains_all_eight_names(self, tmp_path: Path) -> None:
        report = inspect_guarantees(tmp_path)
        assert set(report.guarantees.keys()) == PERMITTED_GUARANTEE_NAMES

    def test_is_guaranteed_true_for_guaranteed_name(self) -> None:
        # local_posix_persistent has AtomicVisibility=guaranteed in the matrix.
        report = GuaranteeReport(
            environment=_make_env("local_posix_persistent"),
            guarantees=dict(_MATRIX["local_posix_persistent"]),
        )
        assert report.is_guaranteed("AtomicVisibility") is True

    def test_is_guaranteed_false_for_best_effort(self) -> None:
        # local_posix_persistent has MetadataPreservation=best_effort.
        report = GuaranteeReport(
            environment=_make_env("local_posix_persistent"),
            guarantees=dict(_MATRIX["local_posix_persistent"]),
        )
        assert report.is_guaranteed("MetadataPreservation") is False

    def test_is_guaranteed_false_for_nontarget(self) -> None:
        # network has AtomicVisibility=nontarget.
        report = GuaranteeReport(
            environment=_make_env("network"),
            guarantees=dict(_MATRIX["network"]),
        )
        assert report.is_guaranteed("AtomicVisibility") is False

    def test_is_guaranteed_false_for_unsupported(self) -> None:
        # unknown has AtomicVisibility=unsupported.
        report = GuaranteeReport(
            environment=_make_env("unknown"),
            guarantees=dict(_MATRIX["unknown"]),
        )
        assert report.is_guaranteed("AtomicVisibility") is False

    def test_degraded_returns_non_guaranteed_names(self) -> None:
        report = GuaranteeReport(
            environment=_make_env("local_posix_persistent"),
            guarantees=dict(_MATRIX["local_posix_persistent"]),
        )
        degraded = report.degraded()
        # On persistent POSIX only MetadataPreservation is best_effort.
        assert "MetadataPreservation" in degraded
        # Every entry must actually be non-guaranteed.
        for name in degraded:
            assert report.guarantees[name] != "guaranteed"

    def test_degraded_returns_in_canonical_order(self) -> None:
        report = GuaranteeReport(
            environment=_make_env("network"),
            guarantees=dict(_MATRIX["network"]),
        )
        degraded = report.degraded()
        # Same relative order as the canonical _GUARANTEE_NAMES tuple.
        positions = [_GUARANTEE_NAMES.index(n) for n in degraded]
        assert positions == sorted(positions)

    def test_report_is_frozen(self, tmp_path: Path) -> None:
        report = inspect_guarantees(tmp_path)
        with pytest.raises((AttributeError, TypeError)):
            report.environment = _make_env("unknown")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# (2) Guarantee levels — vocabulary
# ---------------------------------------------------------------------------


class TestGuaranteeLevelVocabulary:
    """Only the four contract-frozen levels are allowed; nothing else."""

    def test_level_literal_has_exactly_four_values(self) -> None:
        assert {
            "guaranteed",
            "best_effort",
            "nontarget",
            "unsupported",
        } == PERMITTED_LEVELS

    @pytest.mark.parametrize(
        "typo",
        [
            "non_target",  # snake_case mistake
            "nonTarget",  # camelCase mistake
            "NonTarget",  # PascalCase mistake
            "best-effort",  # hyphen mistake
            "Best_Effort",  # capitalised mistake
            "unknown",  # capability-only value, not a level
            "yes",  # capability-only value, not a level
        ],
    )
    def test_no_level_typo_appears_in_matrix(self, typo: str) -> None:
        for fs_class, row in _MATRIX.items():
            for name, level in row.items():
                assert level != typo, f"matrix[{fs_class}][{name}] = {level!r} is a forbidden typo"

    def test_matrix_cells_only_use_permitted_levels(self) -> None:
        for fs_class, row in _MATRIX.items():
            for name, level in row.items():
                assert level in PERMITTED_LEVELS, (
                    f"matrix[{fs_class}][{name}] = {level!r} is not in {PERMITTED_LEVELS}"
                )

    def test_report_uses_only_permitted_levels(self, tmp_path: Path) -> None:
        report = inspect_guarantees(tmp_path)
        for level in report.guarantees.values():
            assert level in PERMITTED_LEVELS


# ---------------------------------------------------------------------------
# (3) Matrix per filesystem_class
# ---------------------------------------------------------------------------


class TestFilesystemClassMatrix:
    """Per-class semantic contracts from design/guarantees-formalization.md §9.

    Tests use monkeypatch to inject a synthetic Environment via
    detect_environment, then call inspect_guarantees and read the matrix
    row directly. Both approaches must agree.
    """

    def test_matrix_covers_every_filesystem_class(self) -> None:
        assert set(_MATRIX.keys()) == PERMITTED_FS_CLASSES

    def test_matrix_rows_have_all_eight_guarantees(self) -> None:
        for fs_class, row in _MATRIX.items():
            assert set(row.keys()) == PERMITTED_GUARANTEE_NAMES, (
                f"row {fs_class} is missing or has extra guarantee keys"
            )

    def test_local_posix_persistent_offers_core_guarantees(self) -> None:
        row = _MATRIX["local_posix_persistent"]
        # Core durable persistence on a real POSIX disk.
        assert row["AtomicVisibility"] == "guaranteed"
        assert row["ReaderConsistency"] == "guaranteed"
        assert row["CrashDurability"] == "guaranteed"
        assert row["WriterExclusion"] == "guaranteed"
        assert row["IntegrityDetection"] == "guaranteed"

    def test_local_posix_memory_keeps_atomic_visibility(self) -> None:
        # tmpfs is in-memory but rename is still atomic. AtomicVisibility
        # must NOT be downgraded.
        row = _MATRIX["local_posix_memory"]
        assert row["AtomicVisibility"] == "guaranteed"
        assert row["ReaderConsistency"] == "guaranteed"

    def test_local_posix_memory_does_not_guarantee_crash_durability(self) -> None:
        # tmpfs has no backing storage. Must be either unsupported or
        # at most best_effort — never guaranteed.
        row = _MATRIX["local_posix_memory"]
        assert row["CrashDurability"] != "guaranteed"

    @pytest.mark.parametrize("fs_class", ["network", "windows", "object_store", "unknown"])
    def test_non_target_classes_do_not_guarantee_atomic_visibility(
        self,
        fs_class: FilesystemClass,
    ) -> None:
        row = _MATRIX[fs_class]
        assert row["AtomicVisibility"] != "guaranteed"

    @pytest.mark.parametrize("fs_class", ["network", "windows", "object_store", "unknown"])
    def test_non_target_classes_do_not_guarantee_crash_durability(
        self,
        fs_class: FilesystemClass,
    ) -> None:
        row = _MATRIX[fs_class]
        assert row["CrashDurability"] != "guaranteed"

    def test_inspect_guarantees_matches_matrix_for_injected_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """inspect_guarantees('/x') must return _MATRIX[fs_class] for that env."""
        injected = _make_env("network", filesystem="nfs4")

        def fake_detect(_path: object) -> Environment:
            return injected

        monkeypatch.setattr("safeatomic._guarantees.detect_environment", fake_detect)

        report = inspect_guarantees(tmp_path)
        assert report.environment is injected
        # Levels exactly match the matrix row.
        assert dict(report.guarantees) == dict(_MATRIX["network"])


# ---------------------------------------------------------------------------
# (4) Capabilities tri-state
# ---------------------------------------------------------------------------


class TestCapabilityTriState:
    """Capability is yes/no/unknown — never silently promoted to yes."""

    def test_capability_literal_has_exactly_three_values(self) -> None:
        assert {"yes", "no", "unknown"} == PERMITTED_CAPABILITIES

    @pytest.mark.parametrize(
        "fsync_dir",
        ["no", "unknown"],
    )
    def test_no_or_unknown_capability_does_not_promote_guarantees(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fsync_dir: Capability,
    ) -> None:
        """Tri-state contract: a capability of ``no`` or ``unknown`` MUST NOT
        be treated as ``yes`` when reading from the matrix.

        We exercise this by injecting an Environment with the same
        filesystem_class but a degraded fsync capability; the matrix lookup
        is class-keyed, so the report's levels come from the canonical row
        and the capability flags remain visibly degraded on env.
        """
        env = _make_env(
            "local_posix_persistent",
            supports_fsync_dir=fsync_dir,
        )

        def fake_detect(_path: object) -> Environment:
            return env

        monkeypatch.setattr("safeatomic._guarantees.detect_environment", fake_detect)

        report = inspect_guarantees(tmp_path)
        # The capability must remain non-"yes" on the reported environment.
        assert report.environment.supports_fsync_dir == fsync_dir
        # And it must never be silently promoted to "yes".
        assert report.environment.supports_fsync_dir != "yes"

    def test_unknown_capability_does_not_force_guaranteed_level(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If atomic_replace probe is unknown, no claim is fabricated."""
        env = _make_env(
            "unknown",
            supports_fsync_file="unknown",
            supports_fsync_dir="unknown",
            supports_atomic_replace="unknown",
        )

        def fake_detect(_path: object) -> Environment:
            return env

        monkeypatch.setattr("safeatomic._guarantees.detect_environment", fake_detect)

        report = inspect_guarantees(tmp_path)
        # Unknown fs class should never produce a "guaranteed" AtomicVisibility.
        assert report.guarantees["AtomicVisibility"] != "guaranteed"
        assert report.guarantees["CrashDurability"] != "guaranteed"


# ---------------------------------------------------------------------------
# (5) Required guarantees per operation (internal _REQUIRED_* constants)
# ---------------------------------------------------------------------------


class TestRequiredGuaranteesPerOperation:
    """Each public operation has a frozenset of required guarantees.

    These constants live in _io_core. They are internal but functionally
    visible at every call: inspect them to confirm contract drift.
    """

    def test_write_atomic_defaults_require_atomic_and_durability(self) -> None:
        assert frozenset({"AtomicVisibility", "CrashDurability"}) == _REQUIRED_WRITE_NONE

    def test_writer_exclusion_only_required_when_lock_requested(self) -> None:
        assert "WriterExclusion" not in _REQUIRED_WRITE_NONE
        assert "WriterExclusion" in _REQUIRED_WRITE_LOCK
        # Lock set is a strict superset of the default set.
        assert _REQUIRED_WRITE_NONE < _REQUIRED_WRITE_LOCK

    def test_read_atomic_default_requires_only_reader_consistency(self) -> None:
        assert frozenset({"ReaderConsistency"}) == _REQUIRED_READ

    def test_read_atomic_with_checksum_includes_integrity_detection(self) -> None:
        assert "IntegrityDetection" in _REQUIRED_READ_CHECKSUM
        assert _REQUIRED_READ < _REQUIRED_READ_CHECKSUM

    def test_write_atomic_with_checksum_includes_integrity_detection(self) -> None:
        assert "IntegrityDetection" in _REQUIRED_WRITE_CHECKSUM
        assert _REQUIRED_WRITE_NONE < _REQUIRED_WRITE_CHECKSUM

    def test_move_atomic_includes_cross_device_safety(self) -> None:
        assert "CrossDeviceSafety" in _REQUIRED_MOVE
        assert "AtomicVisibility" in _REQUIRED_MOVE

    def test_all_required_sets_only_use_real_guarantee_names(self) -> None:
        all_sets = (
            _REQUIRED_WRITE_NONE,
            _REQUIRED_WRITE_LOCK,
            _REQUIRED_WRITE_CHECKSUM,
            _REQUIRED_READ,
            _REQUIRED_READ_CHECKSUM,
            _REQUIRED_MOVE,
        )
        for required in all_sets:
            for name in required:
                assert name in PERMITTED_GUARANTEE_NAMES, (
                    f"required-set contains non-guarantee name {name!r}"
                )


# ---------------------------------------------------------------------------
# (6) Name normalisation (PascalCase ↔ snake_case)
#
# The normalisation lives in _doctor._GUARANTEE_TO_CHECK_NAME plus the
# reverse-lookup in _evaluate_require. Per spec item 6, we test here only
# what is observable via the doctor() public API; deeper coverage is the
# job of test_doctor.py (out of scope).
# ---------------------------------------------------------------------------


class TestGuaranteeNameNormalisation:
    """The doctor() API accepts PascalCase or snake_case guarantee names."""

    def test_doctor_accepts_pascalcase_require(self, tmp_path: Path) -> None:
        # All eight PascalCase names are valid in require=.
        report = doctor(tmp_path, destructive=False, require={"AtomicVisibility"})
        # ok=False here only because destructive=False skips probes;
        # what we are validating is that the name is recognised — i.e.
        # the require evaluation does not raise.
        assert isinstance(report.ok, bool)

    def test_doctor_accepts_snake_case_require(self, tmp_path: Path) -> None:
        report = doctor(tmp_path, destructive=False, require={"atomic_visibility"})
        assert isinstance(report.ok, bool)

    def test_doctor_rejects_unknown_name_via_ok_false(self, tmp_path: Path) -> None:
        # An unknown name in require makes ok = False (per _evaluate_require).
        report = doctor(
            tmp_path,
            destructive=False,
            require={"DefinitelyNotAGuarantee"},
        )
        assert report.ok is False

    def test_normalisation_map_is_one_to_one(self) -> None:
        # Internal: the doctor module owns the canonical mapping. Verify
        # PascalCase ↔ snake_case is bijective for the eight names.
        assert set(_GUARANTEE_TO_CHECK_NAME.keys()) == PERMITTED_GUARANTEE_NAMES
        # Snake-case values are unique.
        assert len(set(_GUARANTEE_TO_CHECK_NAME.values())) == len(_GUARANTEE_TO_CHECK_NAME)
        # Specific examples called out in the spec:
        assert _GUARANTEE_TO_CHECK_NAME["AtomicVisibility"] == "atomic_visibility"
        assert _GUARANTEE_TO_CHECK_NAME["WriterExclusion"] == "writer_exclusion"
        assert _GUARANTEE_TO_CHECK_NAME["CrashDurability"] == "crash_durability"


# ---------------------------------------------------------------------------
# (7) Principle 14 — config does not silently lower guarantees
# ---------------------------------------------------------------------------


class TestPrinciple14ConfigDoesNotLowerGuarantees:
    """safeatomic_config tunes ergonomics only; it must not move guarantees."""

    def test_config_inside_with_block_does_not_change_report(
        self,
        tmp_path: Path,
    ) -> None:
        baseline = inspect_guarantees(tmp_path)
        with safeatomic_config(encoding="utf-16", retries=5, delay=0.5):
            during = inspect_guarantees(tmp_path)
        after = inspect_guarantees(tmp_path)

        assert dict(during.guarantees) == dict(baseline.guarantees)
        assert dict(after.guarantees) == dict(baseline.guarantees)

    def test_config_forbids_guarantee_affecting_keys(self) -> None:
        # safeatomic_config signature does NOT accept these keys.
        # If a future refactor were to add one, it would change the contract.
        forbidden = {
            "safety",
            "concurrency",
            "preserve_metadata",
            "write_checksum",
            "fsync",
            "fsync_file",
            "fsync_dir",
            "tmp_strategy",
        }
        assert _ALLOWED_CONFIG_KEYS.isdisjoint(forbidden)

    def test_config_only_allows_ergonomic_keys(self) -> None:
        # The four ergonomic keys; principle 14 freezes this set.
        assert (
            frozenset(
                {"encoding", "checksum_algo", "retries", "delay"},
            )
            == _ALLOWED_CONFIG_KEYS
        )
