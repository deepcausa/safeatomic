"""Package-level metadata: ``__version__`` and ``__all__``.

These tests pin two invariants visible to library consumers:

* ``__version__`` is the version of the installed distribution. It is
  derived from package metadata (``importlib.metadata.version``) and
  must therefore agree with ``[project].version`` in ``pyproject.toml``
  after build, not be hard-coded in source.
* ``__all__`` is frozen at 43 names (see ADR-0005). The package's
  ``_EXPECTED_PUBLIC_NAMES`` constant enforces this at import time;
  this test makes the assertion explicit at the public boundary.
"""

from __future__ import annotations

import re
from importlib.metadata import version as pkg_version

import safeatomic


def test_version_matches_installed_distribution_metadata() -> None:
    """``safeatomic.__version__`` is sourced from package metadata.

    Regression guard for the v2.0.0 \u2192 v2.0.2 bug where ``__version__``
    was hard-coded in ``src/safeatomic/__init__.py`` and drifted from
    ``pyproject.toml``'s ``[project].version`` across release cuts. The
    fix in v2.0.3 reads from ``importlib.metadata.version`` so the two
    can never disagree once the wheel is built.
    """
    assert safeatomic.__version__ == pkg_version("safeatomic")


def test_version_is_pep440_shaped() -> None:
    """``__version__`` matches a permissive PEP 440 release form.

    The fallback used in source checkouts without dist-info is the
    sentinel ``0.0.0+unknown``; both that and a regular release string
    (e.g. ``2.0.3``, ``2.0.3.dev0``, ``2.0.3rc1``) are accepted.
    """
    pep440 = re.compile(
        r"^\d+(\.\d+){0,3}"  # release
        r"((a|b|rc|\.dev|\.post)\d+)?"  # pre/dev/post
        r"(\+[a-zA-Z0-9.]+)?$"  # local
    )
    assert pep440.match(safeatomic.__version__), (
        f"__version__={safeatomic.__version__!r} is not PEP 440-shaped"
    )


def test_public_surface_size_is_frozen_at_43() -> None:
    """``__all__`` has exactly 43 entries (ADR-0005, asserted at import).

    The runtime assertion in ``safeatomic/__init__.py`` already trips
    on import drift; this test surfaces the invariant in the suite so
    regressions are reported as a normal test failure with a clear
    message rather than only as an ``AssertionError`` at import time.
    """
    assert len(safeatomic.__all__) == 43
    assert len(set(safeatomic.__all__)) == 43, "__all__ has duplicates"
