"""Run TLA+ model checker (TLC) on the safeatomic-project formal models.

Skips cleanly when the formal project directory or the ``tlc`` wrapper is
not available. This is the right behaviour because:

- TLC requires Java and the tla2tools.jar — both are deployment-time
  installs handled by ``apps/safeatomic-project/formal/README.md``.
- The formal models live in a sibling project (``apps/safeatomic-project``)
  and may not be present in every CI checkout.

The test is parametrised over three models. Each invocation has a 30 s
timeout. On TLC failure the captured stdout/stderr is included in the
assertion message so the trace is visible in CI output.

This file does NOT touch any source code, lock tests, doctor tests,
config tests, io_core tests, format tests, or any TLA+ file itself.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


_TLC_WRAPPER = Path.home() / ".local" / "bin" / "tlc"
"""Expected location of the TLC shell wrapper.

The wrapper is created by ``~/.local/bin/install-tlaplus.sh`` (see the
formal README). If absent, tests skip; we do not fall back to a system
``java`` invocation because that would silently bypass the pinned
tla2tools.jar version recorded in the formal README.
"""

_FORMAL_DIR = Path(
    "/home/user/workspace/apps/safeatomic-project/formal",
)
"""Where the formal models live. Hard-coded because the safeatomic
package and the formal models are separate apps under the same monorepo;
discovering the sibling path with relative imports is fragile.
"""

_MODELS: tuple[str, ...] = (
    "SafeAtomicSmoke",
    "SafeAtomicLock",
    "SafeAtomicChecksum",
)

_TIMEOUT_SECONDS: int = 30


# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------


def _skip_if_tlc_missing() -> None:
    """Skip with a clear reason when TLC infrastructure is not present."""
    if not _TLC_WRAPPER.exists():
        pytest.skip(
            f"TLC wrapper not found at {_TLC_WRAPPER}; install via ~/.local/bin/install-tlaplus.sh",
        )
    # Java is required by the wrapper; check it is reachable. The wrapper
    # itself will fail informatively if java is missing, but skipping is
    # nicer than a hard failure.
    if shutil.which("java") is None:
        pytest.skip("java not on PATH; TLC cannot run")


def _skip_if_formal_dir_missing() -> None:
    if not _FORMAL_DIR.is_dir():
        pytest.skip(
            f"formal models directory not present at {_FORMAL_DIR}; "
            "this checkout does not include apps/safeatomic-project",
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", _MODELS)
def test_tla_model_checks_clean(model: str) -> None:
    """Run TLC on a model. Pass iff stdout reports "No error has been found"."""
    _skip_if_tlc_missing()
    _skip_if_formal_dir_missing()

    tla_path = _FORMAL_DIR / f"{model}.tla"
    if not tla_path.is_file():
        pytest.skip(f"model file not found: {tla_path}")

    try:
        result = subprocess.run(  # noqa: S603  # _TLC_WRAPPER is a fixed path under $HOME
            [str(_TLC_WRAPPER), str(tla_path)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"TLC on {model} exceeded {_TIMEOUT_SECONDS}s; "
            "this should not happen for the small v2 models",
        )

    combined_output = result.stdout + "\n" + result.stderr
    assert "No error has been found" in result.stdout, (
        f"TLC reported failure on {model} (exit={result.returncode}):\n{combined_output}"
    )
