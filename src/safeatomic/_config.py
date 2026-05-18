"""Context-local default configuration for safeatomic v2.

This module implements :func:`safeatomic_config`, a ``ContextVar``-backed
context manager that lets callers set per-context defaults for a tightly
scoped subset of keyword arguments:

- ``encoding``
- ``checksum_algo``
- ``retries``
- ``delay``

These four are *ergonomy*: tuning them does not change which guarantees
hold. Keys that *do* affect guarantees (``safety``, ``concurrency``,
``preserve_metadata``, ``write_checksum``, fsync behaviour, tmp strategy)
are deliberately not accepted by :func:`safeatomic_config` and must remain
explicit at each call site.

This restriction is principle 14 of
``apps/safeatomic-project/design/implementation-discipline.md``:

    User-facing configuration MUST NOT silently lower the guarantees that
    :func:`inspect_guarantees` and :func:`doctor` report. The reports are
    the source of truth.

Resolution order for an effective value is:

    explicit kwarg  >  ContextVar (safeatomic_config)  >  hard-coded default

The library uses a private sentinel ``_UNSET`` so that callers can still
pass ``None`` as a meaningful value to an option (e.g. a future
``encoding=None`` for bytes mode). The sentinel is intentionally *not*
exported via ``__all__``.

Thread- and asyncio-safety follows directly from ``contextvars.ContextVar``:
each thread/task has its own view. Nested ``with safeatomic_config(...)``
blocks stack and unwind cleanly via the token-based reset protocol.

Cross-refs:
- ``design/api-v2-proposal.md`` §9 (Configuration)
- ``adr/0005-public-api-surface.md`` (why the surface is scoped)
- ``design/implementation-discipline.md`` principle 14
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class _Unset:
    """Marker type for "argument not supplied".

    A dedicated class (rather than ``None`` or ``object()``) lets type
    checkers see a stable name in error messages and unions, while keeping
    ``None`` available as a genuine value that callers may legitimately pass
    in the future (for example, ``encoding=None`` for bytes mode).
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<UNSET>"


# Typed as ``_Unset``: the singleton is the only inhabitant of its class.
# Callers declare option parameters as ``str | _Unset = _UNSET`` (or similar)
# and dispatch by ``isinstance(value, _Unset)``. The repo bans ``Any`` in
# --strict mode; using the singleton's own type is precise enough.
_UNSET: Final[_Unset] = _Unset()
"""Module-private sentinel for "argument not supplied".

Never exported. Callers compare with :data:`_UNSET` by identity (via
``isinstance(x, _Unset)``) inside this package only.
"""


# ---------------------------------------------------------------------------
# Allowed configuration keys
# ---------------------------------------------------------------------------
#
# This frozenset is the single source of truth for what
# :func:`safeatomic_config` accepts. Adding a key here is an API change and
# must go through an ADR amendment. Removing a key is a breaking change.
#
# Specifically *not* in this set, by design:
#   safety, concurrency, preserve_metadata, write_checksum,
#   fsync (any flavour), tmp strategy, lock payload version.

_ALLOWED_CONFIG_KEYS: Final[frozenset[str]] = frozenset(
    {"encoding", "checksum_algo", "retries", "delay"},
)
"""Keys that :func:`safeatomic_config` may set. Anything else raises.

See ``design/api-v2-proposal.md`` §9.1 and principle 14.
"""


# ---------------------------------------------------------------------------
# ContextVars (one per allowed key)
# ---------------------------------------------------------------------------
#
# Each option gets its own ``ContextVar`` so that nested ``safeatomic_config``
# blocks can override one key without disturbing the others. The default for
# every var is the sentinel ``_UNSET`` ("no context-local override"); the
# library-level hard-coded default is supplied at resolution time by the
# caller via :func:`resolve_config`.

_CV_ENCODING: ContextVar[str | _Unset] = ContextVar(
    "safeatomic.encoding",
    default=_UNSET,
)
_CV_CHECKSUM_ALGO: ContextVar[str | _Unset] = ContextVar(
    "safeatomic.checksum_algo",
    default=_UNSET,
)
_CV_RETRIES: ContextVar[int | _Unset] = ContextVar(
    "safeatomic.retries",
    default=_UNSET,
)
_CV_DELAY: ContextVar[float | _Unset] = ContextVar(
    "safeatomic.delay",
    default=_UNSET,
)


# The lookup table widens to ``object`` because mypy will not unify four
# distinct ``ContextVar[X | _Unset]`` types. The dict is only used for the
# invariant assertion at the bottom of the file; functional code uses the
# named ContextVars directly.
_CV_BY_KEY: Final[dict[str, ContextVar[object]]] = {
    "encoding": cast("ContextVar[object]", _CV_ENCODING),
    "checksum_algo": cast("ContextVar[object]", _CV_CHECKSUM_ALGO),
    "retries": cast("ContextVar[object]", _CV_RETRIES),
    "delay": cast("ContextVar[object]", _CV_DELAY),
}
"""Lookup from option name to its backing ContextVar.

Kept in sync with :data:`_ALLOWED_CONFIG_KEYS` by construction (the same
four names appear in both). A test in ``tests/test_config.py`` asserts
this invariant at import time.
"""


# ---------------------------------------------------------------------------
# Public context manager
# ---------------------------------------------------------------------------


@contextmanager
def safeatomic_config(
    *,
    encoding: str | _Unset = _UNSET,
    checksum_algo: str | _Unset = _UNSET,
    retries: int | _Unset = _UNSET,
    delay: float | _Unset = _UNSET,
) -> Iterator[None]:
    """Set context-local defaults for a scoped subset of options.

    Within the ``with`` block, any call to a safeatomic function that does
    *not* receive an explicit value for one of these four keys will see the
    value supplied here. Calls that *do* pass the keyword explicitly are
    unaffected (explicit always wins, per principle 14).

    Allowed keys: ``encoding``, ``checksum_algo``, ``retries``, ``delay``.

    Forbidden keys (by design):

    - ``safety`` (guarantee gate; must be visible at call site)
    - ``concurrency`` (selects WriterExclusion)
    - ``preserve_metadata`` (affects MetadataPreservation)
    - ``write_checksum`` (selects IntegrityDetection)
    - any fsync or tmp strategy override

    Args:
        encoding: Default text encoding for ``write_atomic`` / ``read_atomic``
            and the format helpers. Omit to leave the outer value in place.
        checksum_algo: Default checksum algorithm name (anything
            :func:`hashlib.new` accepts). Omit to leave the outer value in
            place.
        retries: Default number of additional lock-acquisition attempts.
        delay: Default delay (seconds) between lock-acquisition attempts.

    Yields:
        ``None``. The block establishes context; resolution happens at the
        next function call inside it.

    Raises:
        TypeError: If ``retries`` is not an ``int`` or ``delay`` is not a
            real number; if ``encoding`` or ``checksum_algo`` is not a
            ``str``. Validation happens once on entry rather than on every
            resolution to keep the hot path cheap.

    Examples:
        >>> with safeatomic_config(encoding="utf-16", retries=3):
        ...     write_atomic(p, "hello")   # uses utf-16, retries=3
        ...     write_atomic(q, "world", encoding="utf-8")  # explicit wins
    """
    _validate(
        encoding=encoding,
        checksum_algo=checksum_algo,
        retries=retries,
        delay=delay,
    )

    # Each entry is a no-arg callable that resets one ContextVar to its
    # prior value. We build them with ``functools.partial`` so the captured
    # ``Token[X | _Unset]`` keeps its precise type without needing four
    # separate token lists. ``Callable[[], None]`` unifies all four.
    resets: list[Callable[[], None]] = []
    if not isinstance(encoding, _Unset):
        resets.append(partial(_CV_ENCODING.reset, _CV_ENCODING.set(encoding)))
    if not isinstance(checksum_algo, _Unset):
        resets.append(
            partial(_CV_CHECKSUM_ALGO.reset, _CV_CHECKSUM_ALGO.set(checksum_algo)),
        )
    if not isinstance(retries, _Unset):
        resets.append(partial(_CV_RETRIES.reset, _CV_RETRIES.set(retries)))
    if not isinstance(delay, _Unset):
        resets.append(partial(_CV_DELAY.reset, _CV_DELAY.set(delay)))

    try:
        yield
    finally:
        # Unwind in reverse insertion order so nested ``safeatomic_config``
        # blocks compose correctly.
        for reset in reversed(resets):
            reset()


# ---------------------------------------------------------------------------
# Internal resolution helper
# ---------------------------------------------------------------------------


def resolve_config(
    *,
    encoding: str | _Unset = _UNSET,
    checksum_algo: str | _Unset = _UNSET,
    retries: int | _Unset = _UNSET,
    delay: float | _Unset = _UNSET,
    default_encoding: str = "utf-8",
    default_checksum_algo: str = "sha256",
    default_retries: int = 0,
    default_delay: float = 0.1,
) -> tuple[str, str, int, float]:
    """Resolve effective values for the four configurable keys.

    Resolution order, applied independently for each key:

    1. The ``explicit`` argument, if it is not :data:`_UNSET`.
    2. The corresponding ``ContextVar``, if it has been set inside a
       :func:`safeatomic_config` block.
    3. The hard-coded fallback supplied via the ``default_*`` keyword.

    The result is a four-tuple in fixed order
    ``(encoding, checksum_algo, retries, delay)`` so callers can destructure
    without keyword-argument overhead.

    This function is internal. Public functions accept their own
    ``encoding=``/``checksum_algo=``/``retries=``/``delay=`` arguments using
    the same ``X | _Unset = _UNSET`` pattern and forward them here.

    Args:
        encoding: Caller-supplied value, or :data:`_UNSET`.
        checksum_algo: Caller-supplied value, or :data:`_UNSET`.
        retries: Caller-supplied value, or :data:`_UNSET`.
        delay: Caller-supplied value, or :data:`_UNSET`.
        default_encoding: Library-level default if neither caller nor
            ``ContextVar`` supplies a value.
        default_checksum_algo: Likewise for checksum algorithm.
        default_retries: Likewise for retries.
        default_delay: Likewise for delay.

    Returns:
        A 4-tuple ``(encoding, checksum_algo, retries, delay)`` with no
        ``_UNSET`` values.
    """
    enc: str | _Unset = encoding if not isinstance(encoding, _Unset) else _CV_ENCODING.get()
    enc_final: str = default_encoding if isinstance(enc, _Unset) else enc

    algo: str | _Unset = (
        checksum_algo if not isinstance(checksum_algo, _Unset) else _CV_CHECKSUM_ALGO.get()
    )
    algo_final: str = default_checksum_algo if isinstance(algo, _Unset) else algo

    rtr: int | _Unset = retries if not isinstance(retries, _Unset) else _CV_RETRIES.get()
    rtr_final: int = default_retries if isinstance(rtr, _Unset) else rtr

    dly: float | _Unset = delay if not isinstance(delay, _Unset) else _CV_DELAY.get()
    dly_final: float = default_delay if isinstance(dly, _Unset) else dly

    return (enc_final, algo_final, rtr_final, dly_final)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(
    *,
    encoding: object,
    checksum_algo: object,
    retries: object,
    delay: object,
) -> None:
    """Raise ``TypeError`` if any non-:data:`_UNSET` value has wrong type.

    Validation runs once on context manager entry. The hot path
    (:func:`resolve_config`) does not re-validate.

    Inputs are typed ``object`` (not the narrower ``X | _Unset``) so that
    the runtime checks below remain reachable under mypy --strict and so
    that the function provides real protection against callers that bypass
    type checking (dynamic plumbing in user code).
    """
    if not isinstance(encoding, _Unset) and not isinstance(encoding, str):
        msg = f"safeatomic_config(encoding=...) must be str, got {type(encoding).__name__}"
        raise TypeError(msg)
    if not isinstance(checksum_algo, _Unset) and not isinstance(checksum_algo, str):
        msg = (
            f"safeatomic_config(checksum_algo=...) must be str, got {type(checksum_algo).__name__}"
        )
        raise TypeError(msg)
    if not isinstance(retries, _Unset) and not isinstance(retries, int):
        msg = f"safeatomic_config(retries=...) must be int, got {type(retries).__name__}"
        raise TypeError(msg)
    if not isinstance(delay, _Unset) and not isinstance(delay, (int, float)):
        msg = f"safeatomic_config(delay=...) must be a real number, got {type(delay).__name__}"
        raise TypeError(msg)


# ---------------------------------------------------------------------------
# Invariant guard (catches drift between key list and ContextVar table)
# ---------------------------------------------------------------------------

assert set(_CV_BY_KEY.keys()) == _ALLOWED_CONFIG_KEYS, (  # noqa: S101
    "drift between _ALLOWED_CONFIG_KEYS and _CV_BY_KEY; both must list "
    "exactly the four configurable keys"
)
