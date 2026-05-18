"""Internal constants for safeatomic v2.

These constants are NOT part of the public API. They are not exported via
`safeatomic.__all__`. Refer to `design/api-v2-proposal.md` for the public
surface.

Cross-refs:
- design/api-v2-proposal.md
- design/failure-model.md (sidecar contract)
- design/implementation-discipline.md principle 6 (sidecars are part of the
  protocol)
"""

from __future__ import annotations

from typing import Final, Literal

# ---------------------------------------------------------------------------
# Sidecar file naming
# ---------------------------------------------------------------------------
#
# Lock file lives alongside the target as ``<target>.lock``. It is a sibling,
# not a child, so the parent directory's writability governs both. See
# design/failure-model.md §lock contract.

LOCK_SUFFIX: Final[str] = ".lock"
"""Suffix appended to the target path to derive the lock sidecar path."""

CHECKSUM_SUFFIX: Final[str] = ".sha256"
"""Suffix appended to the target path to derive the checksum sidecar path.

Note: even when a non-default algorithm is used, the suffix remains
``.sha256`` for v2.0. The algorithm is encoded inside the sidecar payload.
A future revision may parametrise the suffix; doing so now would create a
migration burden without benefit.
"""

TMP_PREFIX: Final[str] = ".safeatomic-tmp-"
"""Prefix used for in-flight temporary files.

A distinctive prefix lets external orphan-cleanup processes identify
abandoned temporaries (e.g. left behind after a crash) without false
positives against unrelated files.
"""

TMP_SUFFIX: Final[str] = ".tmp"
"""Suffix used for in-flight temporary files (after the random component)."""


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------
#
# Defaults are intentionally conservative. Callers that need different
# behaviour pass explicit ``retries`` and ``delay`` arguments. The library
# never silently retries beyond what the caller requested.

DEFAULT_RETRIES: Final[int] = 0
"""Default number of retries for lock acquisition (no retry)."""

DEFAULT_DELAY: Final[float] = 0.1
"""Default delay in seconds between lock-acquisition retries."""


# ---------------------------------------------------------------------------
# Checksum
# ---------------------------------------------------------------------------

DEFAULT_CHECKSUM_ALGO: Final[str] = "sha256"
"""Default checksum algorithm.

Acceptable values are the names recognised by :func:`hashlib.new`. The
library does not maintain its own algorithm registry; whatever Python's
standard library accepts, safeatomic accepts.
"""

CHECKSUM_CHUNK_SIZE: Final[int] = 1024 * 1024
"""Read chunk size in bytes for streaming hash computation (1 MiB)."""


# ---------------------------------------------------------------------------
# Lock payload
# ---------------------------------------------------------------------------

LOCK_PAYLOAD_VERSION: Final[int] = 1
"""Version field embedded in the lock-file JSON payload.

Bumping this is a breaking change to the on-disk lock format and would
require coordinated upgrade across consumers. v2.0 ships version 1.
"""


# ---------------------------------------------------------------------------
# Safety policy
# ---------------------------------------------------------------------------

SafetyPolicy = Literal["strict", "warn", "best_effort"]
"""Type alias for the public ``safety`` keyword.

See ``design/guarantees-formalization.md`` §6 and
``design/implementation-discipline.md`` principle 3 for semantics.
"""

DEFAULT_SAFETY: Final[SafetyPolicy] = "strict"
"""Default safety policy: fail-closed. Discussed in
``design/decisions-from-review.md`` (rejected three times in favour of
weaker defaults)."""


# ---------------------------------------------------------------------------
# Concurrency policy
# ---------------------------------------------------------------------------

ConcurrencyPolicy = Literal["none", "lock"]
"""Type alias for the public ``concurrency`` keyword on write paths."""

DEFAULT_CONCURRENCY: Final[ConcurrencyPolicy] = "lock"
"""Default concurrency policy: cooperative whole-file lock.

Rationale recorded in ``design/decisions-from-review.md`` (concurrency
default "none" rejected; safe-by-default is preferred and coherent with
``safety='strict'``).
"""
