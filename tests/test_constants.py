"""Tier 1 tests for safeatomic._constants.

Scope: validate the internal contract of constants used across sidecar
naming, retry defaults, checksum defaults, and policy literals. These
constants are NOT part of the public API; we import them directly from
the private module (deliberate Tier 1 exception).

References:
- design/api-v2-proposal.md
- design/failure-model.md (sidecar contract)
"""

from __future__ import annotations

import hashlib
import typing

from safeatomic import _constants

# ---------------------------------------------------------------------------
# Sidecar file naming
# ---------------------------------------------------------------------------


def test_lock_suffix_is_nonempty_string_starting_with_dot() -> None:
    assert isinstance(_constants.LOCK_SUFFIX, str)
    assert _constants.LOCK_SUFFIX
    # Sidecar suffix appended to a filename must start with '.' or it would
    # merge into the basename (e.g. 'file.jsonlock'). The contract is
    # documented in _paths.py and design/failure-model.md.
    assert _constants.LOCK_SUFFIX.startswith(".")


def test_lock_suffix_contains_lock_token() -> None:
    # The suffix is part of the on-disk protocol; the literal "lock" must
    # appear so external orphan-cleanup tooling can recognise it.
    assert "lock" in _constants.LOCK_SUFFIX.lower()


def test_checksum_suffix_is_nonempty_string_starting_with_dot() -> None:
    assert isinstance(_constants.CHECKSUM_SUFFIX, str)
    assert _constants.CHECKSUM_SUFFIX
    assert _constants.CHECKSUM_SUFFIX.startswith(".")


def test_checksum_suffix_mentions_sha256() -> None:
    # The contract pins the suffix to .sha256 even when a non-default algo
    # is used (algo is encoded inside the sidecar payload). The literal
    # "sha256" is part of the documented contract.
    assert "sha256" in _constants.CHECKSUM_SUFFIX.lower()


def test_tmp_prefix_is_nonempty_and_distinctive() -> None:
    assert isinstance(_constants.TMP_PREFIX, str)
    assert _constants.TMP_PREFIX
    # The library promises a "distinctive prefix" for orphan-cleanup tooling.
    # We don't pin the exact string, but it must include "safeatomic" so
    # external tools can identify it without false positives.
    assert "safeatomic" in _constants.TMP_PREFIX.lower()


def test_tmp_suffix_is_nonempty_string_starting_with_dot() -> None:
    assert isinstance(_constants.TMP_SUFFIX, str)
    assert _constants.TMP_SUFFIX
    assert _constants.TMP_SUFFIX.startswith(".")


def test_sidecar_suffixes_are_distinct() -> None:
    # Lock and checksum sidecars must not collide; if they did, a single
    # file could not be both lock and checksum simultaneously.
    assert _constants.LOCK_SUFFIX != _constants.CHECKSUM_SUFFIX


# ---------------------------------------------------------------------------
# Retry behaviour defaults
# ---------------------------------------------------------------------------


def test_default_retries_is_nonnegative_int() -> None:
    assert isinstance(_constants.DEFAULT_RETRIES, int)
    # 0 is acceptable (documented default: no retry).
    assert _constants.DEFAULT_RETRIES >= 0


def test_default_delay_is_positive_float() -> None:
    assert isinstance(_constants.DEFAULT_DELAY, float)
    # Delay must be strictly positive; a zero delay would defeat the
    # purpose of a retry backoff.
    assert _constants.DEFAULT_DELAY > 0.0


# ---------------------------------------------------------------------------
# Checksum defaults
# ---------------------------------------------------------------------------


def test_default_checksum_algo_includes_sha256() -> None:
    assert isinstance(_constants.DEFAULT_CHECKSUM_ALGO, str)
    assert _constants.DEFAULT_CHECKSUM_ALGO
    # Spec line: "nomes de checksum default incluem sha256 se existir
    # DEFAULT_CHECKSUM_ALGO". The default is sha256.
    assert "sha256" in _constants.DEFAULT_CHECKSUM_ALGO.lower()


def test_default_checksum_algo_is_resolvable_by_hashlib() -> None:
    # If the default algo cannot be instantiated, the whole checksum
    # subsystem is broken at import time.
    h = hashlib.new(_constants.DEFAULT_CHECKSUM_ALGO)
    h.update(b"x")
    assert h.hexdigest()


def test_checksum_chunk_size_is_positive_int() -> None:
    assert isinstance(_constants.CHECKSUM_CHUNK_SIZE, int)
    assert _constants.CHECKSUM_CHUNK_SIZE > 0


# ---------------------------------------------------------------------------
# Lock payload version
# ---------------------------------------------------------------------------


def test_lock_payload_version_is_positive_int() -> None:
    assert isinstance(_constants.LOCK_PAYLOAD_VERSION, int)
    assert _constants.LOCK_PAYLOAD_VERSION >= 1


# ---------------------------------------------------------------------------
# Safety / concurrency policy literals
# ---------------------------------------------------------------------------


def test_default_safety_is_a_safety_policy_value() -> None:
    # SafetyPolicy is a Literal type alias. The default must be one of
    # the literal's permitted values.
    permitted = set(typing.get_args(_constants.SafetyPolicy))
    assert _constants.DEFAULT_SAFETY in permitted


def test_default_safety_includes_strict_value() -> None:
    # 'strict' is documented as the fail-closed default. Even if the
    # default value changes, 'strict' must remain in the literal so
    # callers can opt in explicitly.
    assert "strict" in typing.get_args(_constants.SafetyPolicy)


def test_default_concurrency_is_a_concurrency_policy_value() -> None:
    permitted = set(typing.get_args(_constants.ConcurrencyPolicy))
    assert _constants.DEFAULT_CONCURRENCY in permitted


def test_concurrency_policy_includes_lock_and_none() -> None:
    # Both literals are part of the public API surface (passed as
    # concurrency= kwarg). Removing either is a breaking change.
    args = set(typing.get_args(_constants.ConcurrencyPolicy))
    assert "lock" in args
    assert "none" in args
