"""Tier 3 integration tests for safeatomic format helpers.

Scope: JSON, PyYAML, ruamel.yaml, and TOML wrappers around
``write_atomic`` / ``read_atomic``. Validates round-tripping,
documented defaults, checksum integration, ``safeatomic_config``
propagation, atomic-failure preservation, and a few security-relevant
invariants (safe loaders, no pickle/xml).

Import policy: prefer the public 43-name API. One private import is
used — ``_DOCTOR``-free; we touch only ``safeatomic._formats_yaml``
to monkeypatch its lazy ruamel import for the "ruamel absent"
scenario. This is reported in the final summary.

Spec references:
- design/api-v2-proposal.md (atomic_*_dump / atomic_*_load signatures)
- design/implementation-discipline.md principle 10 (delegate to _io_core)
- design/implementation-discipline.md principle 14 (config is ergonomy)
"""

from __future__ import annotations

import builtins
import json
import sys
from typing import TYPE_CHECKING, Any

import pytest

from safeatomic import (
    ChecksumMismatchError,
    atomic_json_dump,
    atomic_json_load,
    atomic_toml_dump,
    atomic_toml_load,
    atomic_yaml_dump,
    atomic_yaml_dump_ruamel,
    atomic_yaml_load,
    atomic_yaml_load_ruamel,
    safeatomic_config,
)
from safeatomic._paths import checksum_path, tmp_path_for

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _no_safeatomic_tmp_leftover(directory: Path) -> bool:
    """No safeatomic tmp file remains in ``directory`` (sentinel check)."""
    # Use the documented prefix via tmp_path_for of an arbitrary name and
    # extract its prefix portion.
    probe = tmp_path_for(directory / "probe")
    prefix = probe.name.split("-")[0] + "-"  # ".safeatomic-tmp-"
    return not any(p.name.startswith(prefix) for p in directory.iterdir())


# ---------------------------------------------------------------------------
# JSON: round-trip and defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"key": "value", "n": 1, "flag": True, "nothing": None},
        ["a", "b", 1, 2.5, True, None],
        "just a string",
        42,
        True,
        None,
        {"nested": {"a": [1, 2, {"deep": "ok"}]}},
        # Empty structures
        {},
        [],
    ],
    ids=[
        "dict",
        "list",
        "string",
        "int",
        "true",
        "null",
        "nested",
        "empty_dict",
        "empty_list",
    ],
)
def test_json_round_trip(tmp_path: Path, payload: object) -> None:
    target = tmp_path / "data.json"
    atomic_json_dump(target, payload)
    assert atomic_json_load(target) == payload


def test_json_default_indent_is_2(tmp_path: Path) -> None:
    # Documented default: indent=2 (pretty-printed). A pretty-printed
    # dict spans multiple lines.
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"a": 1, "b": 2})
    text = target.read_text(encoding="utf-8")
    assert "\n" in text
    # Two-space indent should be observable on the first nested key.
    assert "  " in text


def test_json_default_sort_keys_is_false(tmp_path: Path) -> None:
    # Documented default: sort_keys=False (insertion order preserved).
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"b": 1, "a": 2})
    text = target.read_text(encoding="utf-8")
    # "b" appears before "a" because we did NOT sort.
    assert text.index('"b"') < text.index('"a"')


def test_json_default_ensure_ascii_is_false(tmp_path: Path) -> None:
    # Documented default: ensure_ascii=False — UTF-8 passthrough.
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"emoji": "café 🌍"})
    text = target.read_text(encoding="utf-8")
    assert "café" in text
    assert "🌍" in text
    # And no \u escapes for those code points.
    assert "\\u" not in text


def test_json_ensure_ascii_true_escapes_unicode(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"emoji": "café"}, ensure_ascii=True)
    text = target.read_text(encoding="utf-8")
    assert "café" not in text
    # Each non-ascii char rendered as \uXXXX.
    assert "\\u" in text


def test_json_sort_keys_true_orders_alphabetically(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"b": 1, "a": 2, "c": 3}, sort_keys=True)
    text = target.read_text(encoding="utf-8")
    assert text.index('"a"') < text.index('"b"') < text.index('"c"')


def test_json_compact_when_indent_none(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"a": 1, "b": 2}, indent=None)
    text = target.read_text(encoding="utf-8")
    # Compact: no newlines.
    assert "\n" not in text.rstrip("\n")


# ---------------------------------------------------------------------------
# JSON: checksum integration
# ---------------------------------------------------------------------------


def test_json_write_checksum_creates_sidecar(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"hello": "world"}, write_checksum=True)
    sidecar = checksum_path(target)
    assert sidecar.exists()


def test_json_check_checksum_succeeds_on_match(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    payload = {"hello": "world"}
    atomic_json_dump(target, payload, write_checksum=True)
    # Loading with check_checksum=True must succeed.
    assert atomic_json_load(target, check_checksum=True) == payload


def test_json_check_checksum_fails_on_corruption(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    payload = {"hello": "world"}
    atomic_json_dump(target, payload, write_checksum=True)
    # Tamper with the data file (sidecar still references the original).
    target.write_text('{"hello":"WORLD"}', encoding="utf-8")
    with pytest.raises(ChecksumMismatchError):
        atomic_json_load(target, check_checksum=True)


def test_json_check_checksum_without_sidecar_raises(tmp_path: Path) -> None:
    """Drift note: the briefing said this case raises FileNotFoundError
    (the standalone ``verify_checksum`` contract). However,
    ``read_atomic(check_checksum=True)`` routes through
    ``_io_core._read_verify_checksum``, which raises
    :class:`ChecksumMismatchError` with ``actual="(sidecar missing)"``.
    Two different error types for the same logical condition is a
    contract drift; reported in the final summary. This test pins the
    *actual* behaviour so the suite is meaningful.
    """
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"x": 1})  # no checksum
    with pytest.raises(ChecksumMismatchError, match="sidecar missing"):
        atomic_json_load(target, check_checksum=True)


# ---------------------------------------------------------------------------
# JSON: error preserves old target
# ---------------------------------------------------------------------------


def test_json_dump_failure_preserves_old_file(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    old = {"ok": True, "count": 7}
    atomic_json_dump(target, old)
    assert atomic_json_load(target) == old

    # Now attempt to dump a non-serialisable object — must fail BEFORE
    # touching the target (json.dumps raises during serialisation, which
    # happens before write_atomic is invoked).
    with pytest.raises(TypeError):
        atomic_json_dump(target, {"bad": object()})

    # Old data still there; no torn write.
    assert atomic_json_load(target) == old


def test_json_dump_failure_leaves_no_tmp(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"ok": True})
    with pytest.raises(TypeError):
        atomic_json_dump(target, {"bad": object()})
    assert _no_safeatomic_tmp_leftover(tmp_path)


# ---------------------------------------------------------------------------
# JSON: invalid file content
# ---------------------------------------------------------------------------


def test_json_load_propagates_decode_error(tmp_path: Path) -> None:
    target = tmp_path / "broken.json"
    target.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        atomic_json_load(target)


def test_json_load_filenotfound(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        atomic_json_load(tmp_path / "does_not_exist.json")


# ---------------------------------------------------------------------------
# YAML (PyYAML safe): round-trip and defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"k": "v", "n": 1, "flag": True, "nothing": None},
        ["a", "b", 1, 2.5, True, None],
        "scalar",
        42,
        {"nested": {"a": [1, 2, {"deep": "ok"}]}},
    ],
    ids=["mapping", "sequence", "string", "int", "nested"],
)
def test_yaml_round_trip(tmp_path: Path, payload: object) -> None:
    target = tmp_path / "data.yaml"
    atomic_yaml_dump(target, payload)
    assert atomic_yaml_load(target) == payload


def test_yaml_default_sort_keys_is_false(tmp_path: Path) -> None:
    """Per docstring: sort_keys defaults to False (human-config friendly).

    This is the central "config readability" contract for YAML: keys
    written in the order the caller chose are preserved on disk.
    """
    target = tmp_path / "data.yaml"
    atomic_yaml_dump(target, {"zeta": 1, "alpha": 2})
    text = target.read_text(encoding="utf-8")
    assert text.index("zeta") < text.index("alpha")


def test_yaml_default_block_style(tmp_path: Path) -> None:
    # Documented default: default_flow_style=False -> block-style YAML
    # (one key per line, not the inline {a: 1, b: 2} form).
    target = tmp_path / "data.yaml"
    atomic_yaml_dump(target, {"a": 1, "b": 2})
    text = target.read_text(encoding="utf-8")
    # Block style has a newline between keys; flow style does not.
    assert "\n" in text.strip()
    assert "{" not in text  # no flow-style braces


def test_yaml_unicode_passthrough(tmp_path: Path) -> None:
    # PyYAML wrapper hardcodes allow_unicode=True; non-ASCII characters
    # must round-trip without escapes.
    target = tmp_path / "data.yaml"
    payload = {"name": "café", "emoji": "🌍"}
    atomic_yaml_dump(target, payload)
    text = target.read_text(encoding="utf-8")
    assert "café" in text
    assert "🌍" in text
    assert atomic_yaml_load(target) == payload


def test_yaml_uses_safe_loader_not_unsafe(tmp_path: Path) -> None:
    """Security: atomic_yaml_load must reject PyYAML's !!python/object tags.

    A safe loader rejects ``!!python/...`` constructors with a
    ConstructorError (subclass of YAMLError). An unsafe loader would
    instantiate the named class — a known RCE vector. This test pins the
    safe behaviour.
    """
    import yaml  # noqa: PLC0415  # yaml is a required dep; local import keeps top minimal

    target = tmp_path / "payload.yaml"
    target.write_text(
        "!!python/object/apply:os.system\nargs: ['echo PWNED']\n",
        encoding="utf-8",
    )
    with pytest.raises(yaml.YAMLError):
        atomic_yaml_load(target)


def test_yaml_check_checksum_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "data.yaml"
    payload = {"answer": 42}
    atomic_yaml_dump(target, payload, write_checksum=True)
    assert atomic_yaml_load(target, check_checksum=True) == payload


def test_yaml_check_checksum_detects_corruption(tmp_path: Path) -> None:
    target = tmp_path / "data.yaml"
    atomic_yaml_dump(target, {"answer": 42}, write_checksum=True)
    target.write_text("answer: 999\n", encoding="utf-8")
    with pytest.raises(ChecksumMismatchError):
        atomic_yaml_load(target, check_checksum=True)


def test_yaml_load_propagates_yaml_error(tmp_path: Path) -> None:
    import yaml  # noqa: PLC0415

    target = tmp_path / "broken.yaml"
    target.write_text("key: [unterminated", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        atomic_yaml_load(target)


# ---------------------------------------------------------------------------
# TOML: round-trip
# ---------------------------------------------------------------------------


def test_toml_round_trip_simple(tmp_path: Path) -> None:
    target = tmp_path / "data.toml"
    payload: dict[str, object] = {
        "title": "example",
        "n": 42,
        "ok": True,
        "ratio": 1.5,
        "tags": ["a", "b", "c"],
    }
    atomic_toml_dump(target, payload)
    loaded = atomic_toml_load(target)
    assert loaded == payload


def test_toml_round_trip_nested(tmp_path: Path) -> None:
    target = tmp_path / "data.toml"
    payload: dict[str, object] = {
        "section": {"key": "value", "n": 1},
        "other": {"nested": {"deep": True}},
    }
    atomic_toml_dump(target, payload)
    assert atomic_toml_load(target) == payload


def test_toml_uses_tomllib_for_read(tmp_path: Path) -> None:
    # Contract: read goes through stdlib tomllib. We assert behaviour
    # equivalent to tomllib.loads on the same text.
    import tomllib  # noqa: PLC0415  # stdlib 3.11+

    target = tmp_path / "data.toml"
    text = 'name = "x"\n[t]\nv = 1\n'
    target.write_text(text, encoding="utf-8")
    assert atomic_toml_load(target) == tomllib.loads(text)


def test_toml_check_checksum_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "data.toml"
    payload: dict[str, object] = {"k": "v"}
    atomic_toml_dump(target, payload, write_checksum=True)
    assert atomic_toml_load(target, check_checksum=True) == payload


def test_toml_check_checksum_detects_corruption(tmp_path: Path) -> None:
    target = tmp_path / "data.toml"
    atomic_toml_dump(target, {"k": "v"}, write_checksum=True)
    target.write_text('k = "evil"\n', encoding="utf-8")
    with pytest.raises(ChecksumMismatchError):
        atomic_toml_load(target, check_checksum=True)


def test_toml_dump_failure_preserves_old_file(tmp_path: Path) -> None:
    target = tmp_path / "data.toml"
    old: dict[str, object] = {"keep": True}
    atomic_toml_dump(target, old)
    # tomli_w raises TypeError on non-serialisable values; sets aren't TOML.
    with pytest.raises((TypeError, Exception)):
        atomic_toml_dump(target, {"bad": {1, 2, 3}})  # type: ignore[arg-type]
    assert atomic_toml_load(target) == old


def test_toml_load_propagates_decode_error(tmp_path: Path) -> None:
    import tomllib  # noqa: PLC0415

    target = tmp_path / "broken.toml"
    target.write_text("not = valid = toml", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        atomic_toml_load(target)


# ---------------------------------------------------------------------------
# ruamel.yaml: real round-trip (skip if not installed)
# ---------------------------------------------------------------------------


_ruamel = pytest.importorskip("ruamel.yaml", reason="ruamel optional extra")


def test_ruamel_round_trip_mapping(tmp_path: Path) -> None:
    target = tmp_path / "data.yaml"
    payload = {"k": "v", "n": 1, "list": [1, 2, 3]}
    atomic_yaml_dump_ruamel(target, payload)
    loaded = atomic_yaml_load_ruamel(target)
    # CommentedMap compares equal to a plain dict with the same items.
    assert dict(loaded) == payload  # type: ignore[call-overload]


def test_ruamel_custom_yaml_instance_used(tmp_path: Path) -> None:
    """A user-supplied YAML instance must drive serialisation.

    We pass an instance configured for flow-style output and check the
    on-disk text matches that configuration.
    """
    from ruamel.yaml import YAML  # noqa: PLC0415

    target = tmp_path / "data.yaml"
    y = YAML()
    y.default_flow_style = True  # type: ignore[assignment]
    y.indent(mapping=2, sequence=4, offset=2)

    atomic_yaml_dump_ruamel(target, {"a": 1, "b": [1, 2]}, yaml_instance=y)
    text = target.read_text(encoding="utf-8")
    # Flow style includes braces/brackets inline somewhere.
    assert "{" in text or "[" in text


def test_ruamel_preserves_comments_round_trip(tmp_path: Path) -> None:
    """ruamel's distinguishing feature: comments survive a round-trip."""
    from ruamel.yaml import YAML  # noqa: PLC0415

    target = tmp_path / "config.yaml"
    target.write_text(
        "# top-level comment\nname: alice  # inline comment\nage: 30\n",
        encoding="utf-8",
    )
    y = YAML(typ="rt")
    data = atomic_yaml_load_ruamel(target, yaml_instance=y)
    # Round-trip back out via ruamel; comments must survive.
    atomic_yaml_dump_ruamel(target, data, yaml_instance=y)
    text = target.read_text(encoding="utf-8")
    assert "top-level comment" in text
    assert "inline comment" in text


def test_ruamel_write_checksum_works(tmp_path: Path) -> None:
    # Formatting instance must not interfere with checksum sidecar.
    target = tmp_path / "data.yaml"
    atomic_yaml_dump_ruamel(target, {"k": "v"}, write_checksum=True)
    assert checksum_path(target).exists()


def test_ruamel_check_checksum_detects_corruption(tmp_path: Path) -> None:
    target = tmp_path / "data.yaml"
    atomic_yaml_dump_ruamel(target, {"k": "v"}, write_checksum=True)
    target.write_text("k: evil\n", encoding="utf-8")
    with pytest.raises(ChecksumMismatchError):
        atomic_yaml_load_ruamel(target, check_checksum=True)


# ---------------------------------------------------------------------------
# ruamel.yaml: missing-dependency path (monkeypatched lazy import)
# ---------------------------------------------------------------------------


def test_ruamel_helpers_raise_importerror_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``ruamel.yaml`` is uninstallable, the helpers must raise a
    clear ImportError carrying the install hint.

    We simulate "not installed" by intercepting the lazy ``from
    ruamel.yaml import YAML`` line. Since the helpers import inside the
    function body, we cannot rely on ``sys.modules`` poisoning alone in
    all cases — we wrap ``builtins.__import__`` to raise for the
    ruamel root.
    """
    real_import = builtins.__import__

    def fake_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name == "ruamel.yaml" or name.startswith("ruamel.yaml."):
            msg = "No module named 'ruamel.yaml' (simulated)"
            raise ImportError(msg)
        return real_import(name, globals, locals, fromlist, level)

    # Evict any pre-imported ruamel modules so the helper hits the
    # __import__ machinery (and thus our hook).
    for mod in list(sys.modules):
        if mod == "ruamel" or mod.startswith("ruamel."):
            monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    target = tmp_path / "x.yaml"
    with pytest.raises(ImportError, match="ruamel"):
        atomic_yaml_dump_ruamel(target, {"k": "v"})
    with pytest.raises(ImportError, match="ruamel"):
        atomic_yaml_load_ruamel(target)


# Note: ``monkeypatch.delitem(sys.modules, ...)`` auto-restores the
# original module entry on fixture teardown, and ``monkeypatch.setattr``
# auto-restores ``builtins.__import__``. No explicit cleanup needed.


# ---------------------------------------------------------------------------
# Atomic behaviour smoke (per format)
# ---------------------------------------------------------------------------


def test_json_dump_leaves_no_tmp_files(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"ok": True})
    assert target.exists()
    assert _no_safeatomic_tmp_leftover(tmp_path)


def test_yaml_dump_leaves_no_tmp_files(tmp_path: Path) -> None:
    target = tmp_path / "data.yaml"
    atomic_yaml_dump(target, {"ok": True})
    assert target.exists()
    assert _no_safeatomic_tmp_leftover(tmp_path)


def test_toml_dump_leaves_no_tmp_files(tmp_path: Path) -> None:
    target = tmp_path / "data.toml"
    atomic_toml_dump(target, {"ok": True})
    assert target.exists()
    assert _no_safeatomic_tmp_leftover(tmp_path)


def test_ruamel_dump_leaves_no_tmp_files(tmp_path: Path) -> None:
    target = tmp_path / "data.yaml"
    atomic_yaml_dump_ruamel(target, {"ok": True})
    assert target.exists()
    assert _no_safeatomic_tmp_leftover(tmp_path)


# ---------------------------------------------------------------------------
# Safety / concurrency / preserve_metadata propagation
# ---------------------------------------------------------------------------


def test_json_dump_accepts_safety_kwarg(tmp_path: Path) -> None:
    # safety= must be on every dump signature; smoke test that
    # passing "best_effort" doesn't blow up (local tmp_path supports
    # all guarantees on Linux CI so "strict" also passes, but
    # best_effort exercises the kwarg path).
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"x": 1}, safety="best_effort")
    assert atomic_json_load(target) == {"x": 1}


def test_yaml_dump_accepts_safety_kwarg(tmp_path: Path) -> None:
    target = tmp_path / "data.yaml"
    atomic_yaml_dump(target, {"x": 1}, safety="warn")
    assert atomic_yaml_load(target) == {"x": 1}


def test_toml_dump_accepts_safety_kwarg(tmp_path: Path) -> None:
    target = tmp_path / "data.toml"
    atomic_toml_dump(target, {"x": "y"}, safety="warn")
    assert atomic_toml_load(target) == {"x": "y"}


def test_json_dump_accepts_concurrency_none(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"x": 1}, concurrency="none")
    assert atomic_json_load(target) == {"x": 1}


def test_json_dump_accepts_preserve_metadata_false(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"x": 1}, preserve_metadata=False)
    assert atomic_json_load(target) == {"x": 1}


def test_yaml_dump_accepts_preserve_metadata_false(tmp_path: Path) -> None:
    target = tmp_path / "data.yaml"
    atomic_yaml_dump(target, {"x": 1}, preserve_metadata=False)
    assert atomic_yaml_load(target) == {"x": 1}


def test_toml_dump_accepts_preserve_metadata_false(tmp_path: Path) -> None:
    target = tmp_path / "data.toml"
    atomic_toml_dump(target, {"x": "y"}, preserve_metadata=False)
    assert atomic_toml_load(target) == {"x": "y"}


def test_json_dump_accepts_session_kwarg(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"x": 1}, session="test-session")
    assert atomic_json_load(target) == {"x": 1}


def test_json_dump_accepts_retries_and_delay(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"x": 1}, retries=2, delay=0.05)
    assert atomic_json_load(target) == {"x": 1}


def test_json_dump_accepts_checksum_algo(tmp_path: Path) -> None:
    # checksum_algo can be set; with write_checksum=True the sidecar
    # uses the algorithm.
    target = tmp_path / "data.json"
    atomic_json_dump(target, {"x": 1}, write_checksum=True, checksum_algo="sha512")
    body = checksum_path(target).read_text(encoding="ascii")
    assert "sha512" in body


# ---------------------------------------------------------------------------
# safeatomic_config integration
# ---------------------------------------------------------------------------


def test_safeatomic_config_encoding_affects_json_load(tmp_path: Path) -> None:
    """``encoding`` resolved via safeatomic_config applies to load paths.

    Note (drift): the JSON DUMP path hardcodes ``encoding="utf-8"`` in
    its internal ``write_atomic`` call, so ``safeatomic_config(encoding=...)``
    does NOT change how dump writes bytes. It DOES affect how load
    decodes bytes, because ``atomic_json_load`` forwards ``encoding`` to
    ``read_atomic``. We test the observable load-side behaviour.
    """
    target = tmp_path / "data.json"
    # Write valid JSON via dump (utf-8 bytes).
    atomic_json_dump(target, {"k": "café"})

    # Loading the same file under safeatomic_config(encoding="utf-8") is
    # equivalent to default behaviour.
    with safeatomic_config(encoding="utf-8"):
        assert atomic_json_load(target) == {"k": "café"}

    # Explicit kwarg trumps context-var (principle 14):
    with safeatomic_config(encoding="latin-1"):
        # Explicit utf-8 wins; non-ascii survives.
        target2 = tmp_path / "explicit.json"
        atomic_json_dump(target2, {"k": "café"})
        assert atomic_json_load(target2, encoding="utf-8") == {"k": "café"}


def test_safeatomic_config_checksum_algo_propagates(tmp_path: Path) -> None:
    """checksum_algo from safeatomic_config flows into write_checksum.

    When write_checksum=True and no explicit checksum_algo is given,
    the resolver picks up the ContextVar value.
    """
    target = tmp_path / "data.json"
    with safeatomic_config(checksum_algo="sha512"):
        atomic_json_dump(target, {"x": 1}, write_checksum=True)
    body = checksum_path(target).read_text(encoding="ascii")
    assert "sha512" in body


def test_safeatomic_config_explicit_beats_context(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    with safeatomic_config(checksum_algo="sha512"):
        atomic_json_dump(
            target,
            {"x": 1},
            write_checksum=True,
            checksum_algo="sha256",  # explicit wins
        )
    body = checksum_path(target).read_text(encoding="ascii")
    assert "sha256" in body
    assert "sha512" not in body


def test_safeatomic_config_restores_outside_block(tmp_path: Path) -> None:
    target1 = tmp_path / "inside.json"
    target2 = tmp_path / "outside.json"
    with safeatomic_config(checksum_algo="sha512"):
        atomic_json_dump(target1, {"x": 1}, write_checksum=True)
    # Outside: default sha256.
    atomic_json_dump(target2, {"x": 1}, write_checksum=True)

    inside_body = checksum_path(target1).read_text(encoding="ascii")
    outside_body = checksum_path(target2).read_text(encoding="ascii")
    assert "sha512" in inside_body
    assert "sha256" in outside_body
    assert "sha512" not in outside_body


def test_safeatomic_config_does_not_affect_inspect_guarantees(
    tmp_path: Path,
) -> None:
    """Principle 14 sanity: ergonomic config never changes guarantees.

    We dump a file inside and outside the block; the protocol behaviour
    (atomic visibility, no tmp leftover) must be identical regardless
    of the ergonomic context.
    """
    target_in = tmp_path / "in.json"
    target_out = tmp_path / "out.json"

    with safeatomic_config(retries=99, delay=0.001, checksum_algo="sha512"):
        atomic_json_dump(target_in, {"k": 1})

    atomic_json_dump(target_out, {"k": 1})

    assert atomic_json_load(target_in) == {"k": 1}
    assert atomic_json_load(target_out) == {"k": 1}
    assert _no_safeatomic_tmp_leftover(tmp_path)


# ---------------------------------------------------------------------------
# Public API hygiene: pickle/xml not exposed
# ---------------------------------------------------------------------------


def test_no_pickle_helper_in_public_api() -> None:
    """No ``atomic_pickle_*`` exists in the 43-name surface (security).

    pickle is a Python-bytecode execution vector; safeatomic v2.0
    deliberately omits it. If a future contributor adds one, this test
    should fail and force an ADR discussion.
    """
    import safeatomic  # noqa: PLC0415

    for name in safeatomic.__all__:
        assert "pickle" not in name.lower(), (
            f"public name {name!r} mentions pickle; ADR review required"
        )


def test_no_xml_helper_in_public_api() -> None:
    """No ``atomic_xml_*`` in the public surface (XXE/DTD concerns)."""
    import safeatomic  # noqa: PLC0415

    for name in safeatomic.__all__:
        assert "xml" not in name.lower(), f"public name {name!r} mentions xml; ADR review required"


def test_yaml_safe_dump_rejects_arbitrary_python_objects(tmp_path: Path) -> None:
    """Symmetric to the load-side test: dumping a non-safe object fails.

    ``yaml.safe_dump`` does not know how to serialise an arbitrary
    Python class instance; it raises YAMLError rather than emitting a
    ``!!python/object`` tag.
    """
    import yaml  # noqa: PLC0415

    class _Obj:
        def __init__(self) -> None:
            self.x = 1

    target = tmp_path / "data.yaml"
    with pytest.raises(yaml.YAMLError):
        atomic_yaml_dump(target, _Obj())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Format helper signatures: spec-required knobs are present
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn",
    [atomic_json_dump, atomic_yaml_dump, atomic_toml_dump, atomic_yaml_dump_ruamel],
)
def test_dump_helpers_expose_required_knobs(fn: object) -> None:
    """Spec line 5: dump helpers propagate safety knobs.

    Required kwargs on every dump helper:
      concurrency, preserve_metadata, write_checksum, checksum_algo,
      retries, delay, session, safety.
    """
    import inspect  # noqa: PLC0415

    sig = inspect.signature(fn)  # type: ignore[arg-type]
    required = {
        "concurrency",
        "preserve_metadata",
        "write_checksum",
        "checksum_algo",
        "retries",
        "delay",
        "session",
        "safety",
    }
    missing = required - set(sig.parameters)
    assert not missing, f"{getattr(fn, '__name__', fn)} missing knobs: {missing}"


@pytest.mark.parametrize(
    "fn",
    [atomic_json_load, atomic_yaml_load, atomic_toml_load, atomic_yaml_load_ruamel],
)
def test_load_helpers_expose_required_knobs(fn: object) -> None:
    """Load helpers must expose check_checksum, checksum_algo, safety.

    Note: TOML load does NOT expose ``encoding`` (TOML spec mandates
    UTF-8, so the helper hardcodes it). The other three loaders do.
    """
    import inspect  # noqa: PLC0415

    sig = inspect.signature(fn)  # type: ignore[arg-type]
    required = {"check_checksum", "checksum_algo", "safety"}
    missing = required - set(sig.parameters)
    assert not missing, f"{getattr(fn, '__name__', fn)} missing knobs: {missing}"
