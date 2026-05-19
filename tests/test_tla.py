"""Run TLA+ model checker (TLC) on the bundled formal models.

The formal models live in the repository's ``formal/`` directory (alongside
``src/`` and ``tests/``). This test runs TLC against each ``.tla`` model
and asserts that TLC reports "No error has been found".

Skip behaviour:

- If the ``formal/`` directory is absent (for example, a stripped-down
  source tarball that excluded it), every test in this module is skipped.
- If neither a ``tlc`` wrapper at ``~/.local/bin/tlc`` nor a ``TLC_JAR``
  environment variable pointing at ``tla2tools.jar`` is available, every
  test is skipped. Java is also required and is checked.

We do not fall back to ``java -jar`` without ``TLC_JAR`` because that
would silently bypass the pinned ``tla2tools.jar`` version recorded in
``formal/README.md``.

This file does NOT touch any source code, lock tests, doctor tests,
config tests, io_core tests, format tests, or any TLA+ file itself.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parent.parent
"""Repo root (the directory that contains ``tests/`` and ``formal/``)."""

_FORMAL_DIR = _REPO_ROOT / "formal"
"""Where the formal models live. Bundled in this repository, not a sibling."""

_TLC_WRAPPER = Path.home() / ".local" / "bin" / "tlc"
"""Default location of the TLC shell wrapper (see ``formal/README.md``)."""

_MODELS: tuple[str, ...] = (
    "SafeAtomicSmoke",
    "SafeAtomicLock",
    "SafeAtomicChecksum",
)

_TIMEOUT_SECONDS: int = 60


# ---------------------------------------------------------------------------
# TLC discovery
# ---------------------------------------------------------------------------


def _tlc_invocation() -> list[str] | None:
    """Return the argv prefix needed to invoke TLC, or ``None`` if unavailable.

    Priority:

    1. ``TLC_JAR`` env var pointing at a ``tla2tools.jar``.
    2. The ``tlc`` wrapper at ``~/.local/bin/tlc``.

    Java is checked separately by the caller (it must be on ``PATH``).
    """
    tlc_jar = os.environ.get("TLC_JAR")
    if tlc_jar and Path(tlc_jar).is_file():
        return ["java", "-cp", tlc_jar, "tlc2.TLC"]
    if _TLC_WRAPPER.is_file() and os.access(_TLC_WRAPPER, os.X_OK):
        return [str(_TLC_WRAPPER)]
    return None


# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------


def _skip_if_formal_dir_missing() -> None:
    if not _FORMAL_DIR.is_dir():
        pytest.skip(
            f"formal models directory not present at {_FORMAL_DIR}; "
            "this source tree does not include the formal/ subtree",
        )


def _skip_if_tlc_missing() -> list[str]:
    """Return the TLC argv prefix, or skip with an explanatory message."""
    invocation = _tlc_invocation()
    if invocation is None:
        pytest.skip(
            f"TLC not available: set TLC_JAR=/path/to/tla2tools.jar, "
            f"or install the wrapper at {_TLC_WRAPPER} "
            "(see formal/README.md)",
        )
    if shutil.which("java") is None:
        pytest.skip("java not on PATH; TLC cannot run")
    return invocation


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", _MODELS)
def test_tla_model_checks_clean(model: str) -> None:
    """Run TLC on a model. Pass iff stdout reports "No error has been found"."""
    _skip_if_formal_dir_missing()
    invocation = _skip_if_tlc_missing()

    tla_path = _FORMAL_DIR / f"{model}.tla"
    if not tla_path.is_file():
        pytest.skip(f"model file not found: {tla_path}")

    try:
        result = subprocess.run(  # noqa: S603  # argv built from a known-safe wrapper/jar path
            [*invocation, str(tla_path)],
            cwd=str(_FORMAL_DIR),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"TLC on {model} exceeded {_TIMEOUT_SECONDS}s; "
            "this should not happen for the bundled v2 models",
        )

    combined_output = result.stdout + "\n" + result.stderr
    assert "No error has been found" in result.stdout, (
        f"TLC reported failure on {model} (exit={result.returncode}):\n{combined_output}"
    )
