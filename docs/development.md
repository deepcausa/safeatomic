# Development guide

This page documents how to reproduce locally the checks that CI runs on
every push: code coverage, TLA+ model-checking of the protocol specs,
and how to read the badges shown in the README.

---

## Running coverage locally

CI measures coverage on every test run and uploads `coverage.xml` to
[Codecov](https://app.codecov.io/gh/deepcausa/safeatomic) from the
Python 3.12 job. You can reproduce the same numbers locally.

### One-shot terminal report

```bash
pytest --cov --cov-report=term-missing:skip-covered
```

`pytest-cov` is already declared under the `test` extra in
`pyproject.toml` (`[project.optional-dependencies]`); a
`pip install -e .[test]` will pull it in along with `pytest`,
`hypothesis`, and `ruamel.yaml`.

`--cov-report=term-missing:skip-covered` mirrors the CI invocation: it
shows missing lines per file while suppressing files that already reach
100% so the output stays scannable.

### HTML report for line-by-line inspection

```bash
pytest --cov --cov-report=html
open htmlcov/index.html   # macOS; on Linux use xdg-open or a browser
```

Each module renders as a clickable file with executed lines in green,
missing lines in red, and partially executed branches in yellow. The
`htmlcov/` directory is git-ignored.

### Machine-readable formats

CI uploads `coverage.xml` (Cobertura format) as a workflow artifact and
also writes `coverage.json` for the in-job summary that posts to
`$GITHUB_STEP_SUMMARY`. You can produce both locally:

```bash
pytest --cov --cov-report=xml --cov-report=json
```

`coverage.json` is git-ignored. `coverage.xml` is not (it is a transient
build artifact and you should not commit it).

### Coverage targets

There is no hard threshold enforced in CI today: the Codecov upload runs
with `fail_ci_if_error: false` to avoid coupling CI to a third-party
availability event. The historical baseline on Python 3.13 is around
**83% statement coverage** with `_capabilities.py` as the weakest file
(its destructive probes are not exercised by the default suite — see
[`doctor.md`](doctor.md) for how to exercise them on your own storage).

---

## Running `doctor` with `destructive=True` in CI

The test suite in CI runs with `destructive=False` (the default) so it
does not exercise the six write probes. To run the full `doctor`
diagnostic with `destructive=True`, use a **temporary directory** such as
`tempfile.gettempdir()` or a tmpfs mount — never a directory that holds
user state.

```python
from safeatomic import doctor
import tempfile, sys

tmp = tempfile.gettempdir()
report = doctor(f"{tmp}/safeatomic-check", destructive=True)
print(report.summary(), file=sys.stderr)
assert report.ok, f"environment check failed: {report.summary()}"
```

In a GitHub Actions workflow, add a step after the normal test job:

```yaml
- name: Destructive environment probe
  run: |
    python -c "
    from safeatomic import doctor, UnsupportedEnvironmentError
    import tempfile, sys
    report = doctor(f'{tempfile.gettempdir()}/safeatomic-check', destructive=True)
    print(report.summary(), file=sys.stderr)
    if not report.ok:
        raise SystemExit(1)
    "
```

This is safe in CI because the temporary directory is cleared between
runs. The probe exercises `create_excl_0600`, `fsync_file`,
`fsync_dir`, `atomic_replace`, `lock_sidecar`, and `checksum_sidecar`
— all of which are reported as `unknown` in the normal test suite.

See [`doctor.md`](doctor.md) for the full reference on `doctor` and the
meaning of each probe.

---

## Running TLA+ model-checking locally

## Running TLA+ model-checking locally

The repository ships three TLC-checkable protocol specs under
[`formal/`](../formal/): `SafeAtomicSmoke`, `SafeAtomicLock`, and
`SafeAtomicChecksum`. See [`formal-models.md`](formal-models.md) for
what each one covers and what it deliberately does not.

### Obtain the TLC tool

`scripts/check-formal.sh` does not assume any particular installation
path. You can either install TLA+ system-wide via your package manager
or download the official jar.

Direct jar (matches the version CI pins):

```bash
mkdir -p ~/.local/opt/tla+
curl -L -o ~/.local/opt/tla+/tla2tools.jar \
  https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar

# Verify the SHA-256 — must match the value pinned in formal/README.md
echo "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88  $HOME/.local/opt/tla+/tla2tools.jar" \
  | sha256sum --check
```

You will also need a Java runtime (the TLA+ project recommends Java 11
or newer; CI uses Temurin 21):

```bash
java -version   # any 11+ JDK or JRE is fine
```

### Run all three models

```bash
TLC_JAR=$HOME/.local/opt/tla+/tla2tools.jar bash scripts/check-formal.sh
```

The script returns exit code `0` if all three models pass and a non-zero
code if any of them fails. It writes per-model output to a temporary
directory by default; pass `--update-reports` to overwrite the canonical
copies under `formal/reports/` (used by maintainers when the model
output changes intentionally).

Expected numbers (matching the committed reports in `formal/reports/`):

| model               | states generated | distinct states | depth |
|---------------------|-----------------:|----------------:|------:|
| SafeAtomicSmoke     |               51 |              15 |     5 |
| SafeAtomicLock      |               28 |               8 |     4 |
| SafeAtomicChecksum  |            1 548 |             259 |     9 |

If TLC or Java are missing, the test that exercises the same models
(`tests/test_tla.py`) skips cleanly with an explanatory message rather
than failing the suite.

### Run a single model manually

For iterating on one spec at a time:

```bash
cd formal
java -jar $TLC_JAR -workers auto -config SafeAtomicLock.cfg SafeAtomicLock.tla
```

---

## README badges

The README ships six status badges across the top. Each one is a real
fact about the current state of the project rather than a vanity
marker:

| badge                  | reports                                                                                       |
|------------------------|-----------------------------------------------------------------------------------------------|
| PyPI version           | latest version published on PyPI; links to the project page                                   |
| Python versions        | versions declared in `pyproject.toml`'s `requires-python` (currently 3.12+)                    |
| CI                     | status of the `CI` workflow on `main` (lint + type-check + matrix tests on 3.12 and 3.13)     |
| Codecov                | coverage percentage as last reported by the Python 3.12 CI job; links to the Codecov project  |
| Formal models (TLA+)   | status of the `Formal models (TLA+)` workflow on `main` (TLC against all three specs)         |
| License                | the MIT licence under which the project is distributed                                        |

The CI and TLA+ badges link directly to the corresponding workflow runs
on GitHub so a red badge can be drilled down in one click.
