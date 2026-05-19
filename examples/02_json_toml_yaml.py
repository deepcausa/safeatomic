"""02 — Structured formats: JSON, TOML, YAML.

The ``atomic_<fmt>_dump`` helpers apply the same atomic write protocol
under the hood, plus the format's standard serialization. The matching
``_load`` helpers read with the same read-as-of-now semantics as
``read_atomic``.

TOML uses stdlib ``tomllib`` (Python 3.11+) for reads and ``tomli_w`` for
writes. YAML ships two variants:

  - atomic_yaml_dump / atomic_yaml_load
        PyYAML; fast, drops comments.
  - atomic_yaml_dump_ruamel / atomic_yaml_load_ruamel
        ruamel.yaml; preserves comments and ordering. Use this when
        editing config files written by humans.

Run:
    python examples/02_json_toml_yaml.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from safeatomic import (
    atomic_json_dump,
    atomic_json_load,
    atomic_toml_dump,
    atomic_toml_load,
    atomic_yaml_dump,
    atomic_yaml_load,
)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        # JSON
        config = {
            "service": "api",
            "port": 8443,
            "workers": 4,
            "features": ["mtls", "h2"],
        }
        atomic_json_dump(base / "config.json", config, indent=2, sort_keys=True)
        print("JSON:", atomic_json_load(base / "config.json"))

        # TOML — note that TOML has no native None / null.
        toml_data = {
            "package": {"name": "demo", "version": "0.1.0"},
            "tools": {"ruff": True, "mypy": True},
        }
        atomic_toml_dump(base / "config.toml", toml_data)
        print("TOML:", atomic_toml_load(base / "config.toml"))

        # YAML
        yaml_data = {
            "clusters": [
                {"name": "dev", "nodes": 3},
                {"name": "prod", "nodes": 12},
            ]
        }
        atomic_yaml_dump(base / "clusters.yaml", yaml_data)
        print("YAML:", atomic_yaml_load(base / "clusters.yaml"))


if __name__ == "__main__":
    main()
