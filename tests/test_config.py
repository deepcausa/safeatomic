"""Tier 1 tests for safeatomic._config / safeatomic_config().

Scope: ContextVar-backed ergonomic configuration.

Import policy: prefer ``from safeatomic import safeatomic_config`` (it
is one of the 43 public names). The private helpers ``resolve_config``
and ``_UNSET`` are imported from ``safeatomic._config`` because the
resolution order specified in the brief (explicit > ContextVar >
default) can only be observed via ``resolve_config`` — public
write/read functions are exercised in a separate Tier 2 suite. This
private import is reported in the final summary.

Spec references:
- design/api-v2-proposal.md §9 (Configuration)
- design/implementation-discipline.md principle 14 (report is truth)
- adr/0005-public-api-surface.md
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from safeatomic import inspect_guarantees, safeatomic_config

# Private helpers: needed to observe resolution order directly. There is
# no public API in v2.0 that exposes the four resolved values without
# also doing I/O. This is the deliberate Tier-1 exception called out in
# the brief.
from safeatomic._config import (
    _ALLOWED_CONFIG_KEYS,
    _CV_BY_KEY,
    _UNSET,
    _Unset,
    resolve_config,
)

# ---------------------------------------------------------------------------
# Sentinel _UNSET
# ---------------------------------------------------------------------------


def test_unset_is_singleton_instance() -> None:
    # The class is exposed; the module guarantees a single inhabitant
    # used by identity across the package.
    assert isinstance(_UNSET, _Unset)


def test_unset_is_distinct_from_none() -> None:
    # Principle: ``None`` must remain a possible legitimate value for a
    # future ``encoding=None`` (bytes mode). The sentinel must therefore
    # not be ``None``.
    assert _UNSET is not None


def test_unset_is_distinct_from_falsy_values() -> None:
    # Identity discrimination matters: callers' isinstance(x, _Unset)
    # checks must not collide with 0, "", False, [].
    falsy_values: tuple[object, ...] = (0, 0.0, "", False, [], (), {})
    for v in falsy_values:
        assert not isinstance(v, _Unset)


def test_unset_repr_is_stable() -> None:
    # repr is part of debugging UX; we don't pin the exact text but it
    # must be non-empty and recognisable.
    r = repr(_UNSET)
    assert isinstance(r, str)
    assert r
    assert "UNSET" in r.upper()


# ---------------------------------------------------------------------------
# Allowed-key invariant (module-level)
# ---------------------------------------------------------------------------


def test_allowed_config_keys_exactly_four() -> None:
    # The frozen v2.0 contract: encoding, checksum_algo, retries, delay.
    # Adding a key is an ADR-amendment-level change.
    assert {"encoding", "checksum_algo", "retries", "delay"} == _ALLOWED_CONFIG_KEYS


def test_cv_by_key_matches_allowed_keys() -> None:
    # Asserted at import time inside _config.py too; we re-check from
    # the test side to detect drift via a regular test failure.
    assert set(_CV_BY_KEY.keys()) == _ALLOWED_CONFIG_KEYS


# ---------------------------------------------------------------------------
# Resolution order: explicit > ContextVar > hard-coded default
# ---------------------------------------------------------------------------


def test_resolve_uses_hard_coded_defaults_when_nothing_set() -> None:
    enc, algo, retries, delay = resolve_config(
        default_encoding="utf-8",
        default_checksum_algo="sha256",
        default_retries=0,
        default_delay=0.1,
    )
    assert (enc, algo, retries, delay) == ("utf-8", "sha256", 0, 0.1)


def test_resolve_uses_context_var_when_no_explicit() -> None:
    with safeatomic_config(encoding="latin-1", retries=5):
        enc, algo, retries, delay = resolve_config(
            default_encoding="utf-8",
            default_checksum_algo="sha256",
            default_retries=0,
            default_delay=0.1,
        )
    # encoding/retries: ContextVar wins. checksum_algo/delay: hard-coded
    # default (no ContextVar override active for them).
    assert enc == "latin-1"
    assert retries == 5
    assert algo == "sha256"
    assert delay == pytest.approx(0.1)


def test_resolve_explicit_beats_context_var() -> None:
    with safeatomic_config(encoding="latin-1", retries=5):
        enc, _algo, retries, _delay = resolve_config(
            encoding="utf-16",
            retries=99,
            default_encoding="utf-8",
            default_checksum_algo="sha256",
            default_retries=0,
            default_delay=0.1,
        )
    assert enc == "utf-16"
    assert retries == 99


def test_resolve_explicit_beats_default_when_no_context() -> None:
    enc, _algo, _retries, _delay = resolve_config(
        encoding="ascii",
        default_encoding="utf-8",
        default_checksum_algo="sha256",
        default_retries=0,
        default_delay=0.1,
    )
    assert enc == "ascii"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # encoding axis
        ({"encoding": "ascii"}, "ascii"),
        # checksum_algo axis (kwarg "explicit" wins)
    ],
)
def test_resolve_handles_each_axis_independently(
    kwargs: dict[str, object],
    expected: str,
) -> None:
    enc, _a, _r, _d = resolve_config(
        **kwargs,  # type: ignore[arg-type]
        default_encoding="utf-8",
        default_checksum_algo="sha256",
        default_retries=0,
        default_delay=0.1,
    )
    assert enc == expected


def test_resolve_checksum_algo_resolution_order() -> None:
    # default
    _e, algo, _r, _d = resolve_config(
        default_encoding="utf-8",
        default_checksum_algo="sha256",
        default_retries=0,
        default_delay=0.1,
    )
    assert algo == "sha256"
    # context-var override
    with safeatomic_config(checksum_algo="sha512"):
        _e, algo, _r, _d = resolve_config(
            default_encoding="utf-8",
            default_checksum_algo="sha256",
            default_retries=0,
            default_delay=0.1,
        )
        assert algo == "sha512"
        # explicit wins
        _e, algo, _r, _d = resolve_config(
            checksum_algo="blake2b",
            default_encoding="utf-8",
            default_checksum_algo="sha256",
            default_retries=0,
            default_delay=0.1,
        )
        assert algo == "blake2b"


def test_resolve_delay_resolution_order() -> None:
    _e, _a, _r, delay = resolve_config(
        default_encoding="utf-8",
        default_checksum_algo="sha256",
        default_retries=0,
        default_delay=0.25,
    )
    assert delay == pytest.approx(0.25)
    with safeatomic_config(delay=1.5):
        _e, _a, _r, delay = resolve_config(
            default_encoding="utf-8",
            default_checksum_algo="sha256",
            default_retries=0,
            default_delay=0.25,
        )
        assert delay == pytest.approx(1.5)
        _e, _a, _r, delay = resolve_config(
            delay=9.0,
            default_encoding="utf-8",
            default_checksum_algo="sha256",
            default_retries=0,
            default_delay=0.25,
        )
        assert delay == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# Context manager: scope, restoration, nesting
# ---------------------------------------------------------------------------


def test_context_manager_restores_outside_block() -> None:
    # Outside any block, ContextVar returns _UNSET → resolution falls to
    # hard-coded default.
    with safeatomic_config(encoding="latin-1"):
        pass
    enc, _a, _r, _d = resolve_config(
        default_encoding="utf-8",
        default_checksum_algo="sha256",
        default_retries=0,
        default_delay=0.1,
    )
    assert enc == "utf-8"


def test_context_manager_applies_inside_block() -> None:
    with safeatomic_config(encoding="latin-1"):
        enc, _a, _r, _d = resolve_config(
            default_encoding="utf-8",
            default_checksum_algo="sha256",
            default_retries=0,
            default_delay=0.1,
        )
        assert enc == "latin-1"


def test_context_manager_nesting_inner_override_then_restore() -> None:
    with safeatomic_config(encoding="latin-1"):
        enc, *_ = resolve_config(
            default_encoding="utf-8",
            default_checksum_algo="sha256",
            default_retries=0,
            default_delay=0.1,
        )
        assert enc == "latin-1"

        with safeatomic_config(encoding="utf-16"):
            enc, *_ = resolve_config(
                default_encoding="utf-8",
                default_checksum_algo="sha256",
                default_retries=0,
                default_delay=0.1,
            )
            assert enc == "utf-16"

        # After inner block: must restore to outer (latin-1), NOT to
        # hard-coded default.
        enc, *_ = resolve_config(
            default_encoding="utf-8",
            default_checksum_algo="sha256",
            default_retries=0,
            default_delay=0.1,
        )
        assert enc == "latin-1"

    # After outer block: hard-coded default.
    enc, *_ = resolve_config(
        default_encoding="utf-8",
        default_checksum_algo="sha256",
        default_retries=0,
        default_delay=0.1,
    )
    assert enc == "utf-8"


def test_context_manager_nesting_independent_keys() -> None:
    # Outer sets encoding; inner sets retries. Each key independent.
    with safeatomic_config(encoding="latin-1"):
        with safeatomic_config(retries=7):
            enc, _a, retries, _d = resolve_config(
                default_encoding="utf-8",
                default_checksum_algo="sha256",
                default_retries=0,
                default_delay=0.1,
            )
            assert enc == "latin-1"
            assert retries == 7
        # Inner exited: retries restored, encoding preserved
        enc, _a, retries, _d = resolve_config(
            default_encoding="utf-8",
            default_checksum_algo="sha256",
            default_retries=0,
            default_delay=0.1,
        )
        assert enc == "latin-1"
        assert retries == 0


def test_context_manager_restores_on_exception() -> None:
    class _BoomError(Exception):
        pass

    with pytest.raises(_BoomError), safeatomic_config(encoding="latin-1", retries=42):
        raise _BoomError

    # After exception unwind, no context-local value should leak.
    enc, _a, retries, _d = resolve_config(
        default_encoding="utf-8",
        default_checksum_algo="sha256",
        default_retries=0,
        default_delay=0.1,
    )
    assert enc == "utf-8"
    assert retries == 0


def test_context_manager_passes_none_means_no_override() -> None:
    # Calling safeatomic_config() with no kwargs is a no-op block:
    # everything resolves to hard-coded defaults inside and outside.
    with safeatomic_config():
        enc, algo, retries, delay = resolve_config(
            default_encoding="utf-8",
            default_checksum_algo="sha256",
            default_retries=0,
            default_delay=0.1,
        )
    assert (enc, algo, retries, delay) == ("utf-8", "sha256", 0, 0.1)


# ---------------------------------------------------------------------------
# Forbidden keys
# ---------------------------------------------------------------------------

# Per principle 14, these keys affect guarantees and must remain
# explicit at every call site. ``safeatomic_config`` MUST refuse them.
_FORBIDDEN_KEYS = (
    "safety",
    "guarantee_policy",
    "concurrency",
    "fsync_file",
    "fsync_dir",
    "preserve_metadata",
    "tmp_prefix",
    "tmp_suffix",
    "tmp_mode",
)


@pytest.mark.parametrize("key", _FORBIDDEN_KEYS)
def test_forbidden_keys_raise_typeerror(key: str) -> None:
    # ``safeatomic_config`` is a keyword-only signature; any unknown
    # keyword raises TypeError at call time before the generator is
    # ever advanced. This is exactly what the spec demands ("TypeError
    # or erro claro").
    with pytest.raises(TypeError):
        # We pass via **kwargs to keep this parametric.
        safeatomic_config(**{key: "x"})  # type: ignore[arg-type]


def test_forbidden_keys_not_in_allowed_set() -> None:
    # Defence in depth: the allowed-key whitelist is the single source
    # of truth; none of the forbidden names may leak into it.
    for k in _FORBIDDEN_KEYS:
        assert k not in _ALLOWED_CONFIG_KEYS


# ---------------------------------------------------------------------------
# Type validation
# ---------------------------------------------------------------------------


def test_validation_rejects_non_str_encoding() -> None:
    with pytest.raises(TypeError), safeatomic_config(encoding=123):  # type: ignore[arg-type]
        pass


def test_validation_rejects_non_str_checksum_algo() -> None:
    with pytest.raises(TypeError), safeatomic_config(checksum_algo=object()):  # type: ignore[arg-type]
        pass


def test_validation_rejects_non_int_retries() -> None:
    with pytest.raises(TypeError), safeatomic_config(retries="three"):  # type: ignore[arg-type]
        pass


def test_validation_rejects_non_numeric_delay() -> None:
    with pytest.raises(TypeError), safeatomic_config(delay="slow"):  # type: ignore[arg-type]
        pass


def test_validation_accepts_int_for_delay() -> None:
    # int is a subtype of "real number" per the docstring; an int delay
    # must be accepted (not coerced to error).
    with safeatomic_config(delay=2):
        _e, _a, _r, delay = resolve_config(
            default_encoding="utf-8",
            default_checksum_algo="sha256",
            default_retries=0,
            default_delay=0.1,
        )
        assert delay == 2


# ---------------------------------------------------------------------------
# ContextVar isolation between threads
# ---------------------------------------------------------------------------


def test_context_does_not_leak_to_other_thread() -> None:
    """A ContextVar set in thread A must not be visible in thread B.

    contextvars.copy_context() is used implicitly by threading: a new
    thread starts with the *current* context of the caller AT THREAD
    CREATION TIME. So we set the value AFTER the worker thread is
    already inside its barrier.
    """
    barrier = threading.Barrier(2)
    observed: dict[str, str] = {}

    def worker() -> None:
        # Worker arrives at barrier with its own (default) context.
        barrier.wait()
        # Main thread is now inside the with-block. Worker resolves
        # independently and must see the hard-coded default.
        enc, _a, _r, _d = resolve_config(
            default_encoding="utf-8",
            default_checksum_algo="sha256",
            default_retries=0,
            default_delay=0.1,
        )
        observed["worker"] = enc
        # Release main thread.
        barrier.wait()

    t = threading.Thread(target=worker)
    t.start()
    # Main thread sets a context value, then releases worker, then waits
    # until worker has observed its own context.
    with safeatomic_config(encoding="latin-1"):
        barrier.wait()  # release worker into the observation window
        # Main observation:
        enc_main, *_ = resolve_config(
            default_encoding="utf-8",
            default_checksum_algo="sha256",
            default_retries=0,
            default_delay=0.1,
        )
        barrier.wait()  # wait for worker to finish observation
    t.join(timeout=2.0)
    assert not t.is_alive(), "worker thread did not finish in time"

    assert enc_main == "latin-1"
    # Worker had no override → must see the hard-coded default.
    assert observed["worker"] == "utf-8"


def test_concurrent_blocks_in_thread_pool_do_not_leak() -> None:
    """Run multiple workers, each with its own block, and verify isolation."""

    def task(value: str) -> str:
        with safeatomic_config(encoding=value):
            enc, *_ = resolve_config(
                default_encoding="utf-8",
                default_checksum_algo="sha256",
                default_retries=0,
                default_delay=0.1,
            )
            return enc

    values = ["latin-1", "utf-16", "ascii", "utf-32"] * 4
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(task, values))
    assert results == values


# ---------------------------------------------------------------------------
# Principle 14: ergonomy only — guarantees must not move
# ---------------------------------------------------------------------------


def test_inspect_guarantees_unaffected_by_config_block(tmp_path: Path) -> None:
    """The four config keys are ergonomy. Toggling them inside a
    ``safeatomic_config`` block MUST NOT change what
    ``inspect_guarantees`` reports for the same path.

    This is the central principle 14 check: the report is the truth.
    """
    before = inspect_guarantees(tmp_path)
    with safeatomic_config(
        encoding="latin-1",
        checksum_algo="sha512",
        retries=99,
        delay=5.0,
    ):
        inside = inspect_guarantees(tmp_path)
    after = inspect_guarantees(tmp_path)

    # GuaranteeReport is a frozen dataclass; equality is structural.
    assert inside == before
    assert after == before


def test_config_only_advertises_ergonomy_keys() -> None:
    """The whitelist must not silently accept any guarantee-affecting key.

    Cross-check between the implementation's whitelist and the design
    invariant that the whitelist contains exactly the four ergonomic
    keys. If a future change widens this set, principle 14 must be
    re-evaluated and this test updated together with the ADR.
    """
    ergonomy_only = {"encoding", "checksum_algo", "retries", "delay"}
    assert ergonomy_only == _ALLOWED_CONFIG_KEYS
