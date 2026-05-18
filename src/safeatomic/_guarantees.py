"""Guarantee inspection for safeatomic v2.

Exposes :func:`inspect_guarantees`, which maps an :class:`Environment`
to a :class:`GuaranteeReport`. The report says, for each of the eight
guarantees, what level the library can provide on that environment.

This module implements the normative matrix from
``design/guarantees-formalization.md`` §9. Changes to the matrix MUST
go through an ADR amendment per ``design/implementation-discipline.md``
principle 11.

Cross-refs:
- design/guarantees-formalization.md §1 (the G relation)
- design/guarantees-formalization.md §3 (the eight guarantees)
- design/guarantees-formalization.md §4 (levels)
- design/guarantees-formalization.md §9 (filesystem-class matrix)
- design/guarantees-formalization.md §10 (inspection API)
- design/implementation-discipline.md principle 2 (per-op required guarantees)
- design/implementation-discipline.md principle 14 (report is source of truth)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from safeatomic._capabilities import (
    Environment,
    FilesystemClass,
    detect_environment,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from os import PathLike

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

GuaranteeLevel = Literal["guaranteed", "best_effort", "nontarget", "unsupported"]
"""One of four levels per guarantee.

See ``design/guarantees-formalization.md`` §4.

- ``guaranteed``: the library provides this guarantee on this environment.
- ``best_effort``: the library attempts to provide it but cannot confirm.
- ``nontarget``: the environment is outside the v2.0 target set; the
  library does not claim this guarantee here.
- ``unsupported``: the environment cannot provide this guarantee
  *by construction* (e.g. CrashDurability on tmpfs).
"""

GuaranteeName = Literal[
    "AtomicVisibility",
    "ReaderConsistency",
    "CrashDurability",
    "WriterExclusion",
    "StaleRecovery",
    "IntegrityDetection",
    "MetadataPreservation",
    "CrossDeviceSafety",
]
"""The eight named guarantees defined in design/guarantees-formalization.md §3."""


_GUARANTEE_NAMES: Final[tuple[GuaranteeName, ...]] = (
    "AtomicVisibility",
    "ReaderConsistency",
    "CrashDurability",
    "WriterExclusion",
    "StaleRecovery",
    "IntegrityDetection",
    "MetadataPreservation",
    "CrossDeviceSafety",
)

# ---------------------------------------------------------------------------
# Normative matrix: filesystem_class -> guarantee -> level
# ---------------------------------------------------------------------------
#
# This is the canonical translation of design/guarantees-formalization.md §9.
# Rows are guarantee names, columns are filesystem classes. Each cell is
# the level that the library claims for write_atomic with default options
# on that class.
#
# CHANGES TO THIS TABLE REQUIRE AN ADR AMENDMENT. The values here must
# match the table in §9 exactly.

_MATRIX: Final[dict[FilesystemClass, dict[GuaranteeName, GuaranteeLevel]]] = {
    "local_posix_persistent": {
        "AtomicVisibility": "guaranteed",
        "ReaderConsistency": "guaranteed",
        "CrashDurability": "guaranteed",
        "WriterExclusion": "guaranteed",
        "StaleRecovery": "guaranteed",
        "IntegrityDetection": "guaranteed",
        "MetadataPreservation": "best_effort",
        "CrossDeviceSafety": "guaranteed",
    },
    "local_posix_memory": {
        "AtomicVisibility": "guaranteed",
        "ReaderConsistency": "guaranteed",
        # tmpfs has no backing storage; CrashDurability is unsupported by
        # construction, not non-target.
        "CrashDurability": "unsupported",
        "WriterExclusion": "guaranteed",
        "StaleRecovery": "guaranteed",
        "IntegrityDetection": "guaranteed",
        "MetadataPreservation": "best_effort",
        "CrossDeviceSafety": "guaranteed",
    },
    "network": {
        "AtomicVisibility": "nontarget",
        "ReaderConsistency": "nontarget",
        "CrashDurability": "nontarget",
        "WriterExclusion": "nontarget",
        "StaleRecovery": "nontarget",
        # algorithm correctness is fs-independent; sidecar ordering degrades
        # on network FS, hence BestEffort overall.
        "IntegrityDetection": "best_effort",
        "MetadataPreservation": "nontarget",
        "CrossDeviceSafety": "guaranteed",
    },
    "windows": {
        "AtomicVisibility": "nontarget",
        "ReaderConsistency": "nontarget",
        "CrashDurability": "nontarget",
        "WriterExclusion": "nontarget",
        "StaleRecovery": "nontarget",
        "IntegrityDetection": "best_effort",
        "MetadataPreservation": "nontarget",
        "CrossDeviceSafety": "guaranteed",
    },
    "object_store": {
        "AtomicVisibility": "nontarget",
        "ReaderConsistency": "nontarget",
        "CrashDurability": "nontarget",
        "WriterExclusion": "nontarget",
        "StaleRecovery": "nontarget",
        "IntegrityDetection": "best_effort",
        "MetadataPreservation": "nontarget",
        "CrossDeviceSafety": "guaranteed",
    },
    "unknown": {
        "AtomicVisibility": "unsupported",
        "ReaderConsistency": "unsupported",
        "CrashDurability": "unsupported",
        "WriterExclusion": "unsupported",
        "StaleRecovery": "unsupported",
        # checksum math is fs-independent, so BestEffort even on unknown.
        "IntegrityDetection": "best_effort",
        "MetadataPreservation": "unsupported",
        "CrossDeviceSafety": "guaranteed",
    },
}


# ---------------------------------------------------------------------------
# GuaranteeReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GuaranteeReport:
    """Per-path snapshot of the eight guarantees and their levels.

    Produced by :func:`inspect_guarantees`. The report is the source of
    truth for what the library is willing to claim on this environment.
    Any future config-driven degradation (per
    ``design/implementation-discipline.md`` principle 14) must be reflected
    in this report.

    Attributes:
        environment: The :class:`Environment` from which the levels were
            derived.
        guarantees: Mapping from guarantee name to level. Always contains
            all eight names.

    Examples:
        >>> from safeatomic import inspect_guarantees
        >>> report = inspect_guarantees("/tmp/state.json")
        >>> report.is_guaranteed("AtomicVisibility")
        True
        >>> report.degraded()
        ['MetadataPreservation']  # always BestEffort by copystat semantics
    """

    environment: Environment
    guarantees: Mapping[GuaranteeName, GuaranteeLevel]

    def is_guaranteed(self, name: GuaranteeName) -> bool:
        """Return ``True`` if the named guarantee is at level ``guaranteed``.

        Args:
            name: One of the eight guarantee names.

        Returns:
            ``True`` only when the level is exactly ``"guaranteed"``.
            ``best_effort``, ``nontarget``, and ``unsupported`` all
            return ``False``.

        Raises:
            KeyError: If ``name`` is not a valid guarantee name.
        """
        return self.guarantees[name] == "guaranteed"

    def degraded(self) -> list[GuaranteeName]:
        """Return guarantees whose level is below ``guaranteed``.

        Returns:
            List of guarantee names at any level other than ``guaranteed``,
            in canonical order.
        """
        return [name for name in _GUARANTEE_NAMES if self.guarantees[name] != "guaranteed"]

    def __str__(self) -> str:
        """Return a one-line summary suitable for logs."""
        env = self.environment
        degraded = self.degraded()
        if not degraded:
            tail = "all guaranteed"
        else:
            tail = "degraded: " + ", ".join(f"{n}={self.guarantees[n]}" for n in degraded)
        return f"GuaranteeReport(platform={env.platform}, fs_class={env.filesystem_class}, {tail})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def inspect_guarantees(path: str | PathLike[str]) -> GuaranteeReport:
    """Return a :class:`GuaranteeReport` for the given path's environment.

    The report describes what level of each guarantee the library can
    provide on the filesystem where ``path`` resides. The result is
    cached by ``st_dev`` (mount-stable) per :mod:`safeatomic._capabilities`.

    Args:
        path: The path whose environment is to be inspected. The path
            does not have to exist; the nearest existing ancestor is
            used to determine the filesystem.

    Returns:
        A frozen :class:`GuaranteeReport` with the environment snapshot
        and the eight guarantee levels.

    Examples:
        >>> from safeatomic import inspect_guarantees
        >>> report = inspect_guarantees("/var/lib/myapp/state.json")
        >>> if not report.is_guaranteed("CrashDurability"):
        ...     raise RuntimeError("environment does not provide crash durability")

    Cross-ref:
        ``design/guarantees-formalization.md`` §9, §10.
    """
    env = detect_environment(path)
    levels = dict(_MATRIX[env.filesystem_class])
    return GuaranteeReport(environment=env, guarantees=levels)
