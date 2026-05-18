# Contributing to safeatomic

Thank you for considering a contribution. This document covers what you
need to know to submit a useful change.

## Before you start

- For non-trivial changes, open an issue first to discuss the approach.
  This saves both your time and ours.
- For security issues, see [`SECURITY.md`](SECURITY.md) — do not open a
  public issue or PR for vulnerabilities.

## Scope

`safeatomic` is deliberately small. Before proposing a feature, check
whether it fits the project's positioning:

- **Yes**: features that strengthen, formalise, or extend the eight
  documented guarantees on supported filesystems.
- **Maybe**: new format helpers, new exception types, new inspection
  surfaces. Discuss in an issue first.
- **No**: features that move the library toward becoming a database,
  a distributed coordination primitive, or a schema/encryption/compression
  layer. There are better-suited libraries for those concerns.

When in doubt, propose in an issue and we will help classify.

## Development setup

```bash
# Clone
git clone <repo-url> safeatomic
cd safeatomic

# Create a virtualenv (Python 3.12+)
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode with dev extras
pip install -e ".[dev,test]"
```

## Running checks

```bash
ruff check                     # lint
ruff format --check            # format check
mypy --strict src/             # types
pytest                         # tests (fast suite)
pytest -m slow                 # property/invariant tests
```

All four must pass for a PR to be merged.

## Tests

- Test the **public API**, not internal modules. Tests under `tests/internal/`
  are reserved for implementation invariants and must stay small.
- Test **invariants**, not implementations. A test that asserts
  `os.replace` was called once is testing the mock, not the behaviour.
- Every fix must include a test that fails without the fix and passes
  with it.
- New features must include tests for happy path, error paths, and
  every `safety` policy value.

See the project's testing strategy doc if you need depth.

## Style

- **Format**: `ruff format` (configured in `pyproject.toml`).
- **Lint**: `ruff check`. We use a strict ruleset.
- **Types**: full type hints on all public callables. Internal helpers
  may use inference but `mypy --strict` must pass.
- **Naming**:
  - Public: descriptive snake_case for functions, PascalCase for types.
    No abbreviations except widely understood ones (`http`, `json`).
  - Internal: same conventions, underscore-prefixed if module-level.
- **Imports**: stdlib → third-party → first-party, alphabetised within
  each group. Ruff enforces this.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/) format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types:

- `feat` — new feature
- `fix` — bug fix
- `docs` — documentation only
- `refactor` — code change that neither fixes a bug nor adds a feature
- `test` — adding or correcting tests
- `chore` — build, tooling, dependencies
- `perf` — performance improvement (must include benchmark evidence)

Scopes (informational):

- `io`, `locks`, `checksum`, `formats`, `safety`, `guarantees`, `cli`, `types`, `docs`, `tests`, `build`

Subject in imperative mood, lowercase, no trailing period.

## Guarantee changes

Changes that affect the guarantee matrix require special handling:

1. Update `design/guarantees-formalization.md` § 9 (in the
   `safeatomic-project` repo) with the new cell value.
2. Update `CHANGELOG.md` under `Guarantees` (separate section, not
   `Changed`).
3. If the change *weakens* a guarantee level, this is a major version
   bump (see ADR-0006). Discuss in an issue before opening the PR.
4. Add or update tests in `tests/invariants/` and `tests/filesystems/`
   that exercise the new behaviour.

## Documentation

User-facing documentation lives in `docs/` in this repository. Internal
design rationale lives in the private `safeatomic-project` repository.

If your change affects user-visible behaviour, update:

- The relevant section of `README.md`
- `docs/api.md` if signatures or behaviour change
- `CHANGELOG.md` under the appropriate section
- The docstring of the affected callable

## Pull requests

Checklist before submitting:

- [ ] Branch is rebased on the latest `main`
- [ ] All checks pass locally (`ruff`, `mypy --strict`, `pytest`)
- [ ] Tests added or updated
- [ ] `CHANGELOG.md` updated under `Unreleased`
- [ ] Docstrings updated if signatures changed
- [ ] Commit messages follow Conventional Commits

The PR description should explain:

- What the change does
- Why it is needed
- What testing was performed
- Any guarantee-matrix implications (link to ADR-0006 if relevant)

## Code of conduct

Be respectful. Disagree with ideas, not people. Assume good faith in
reviewers and contributors.

## License

By contributing, you agree that your contributions are licensed under
the same [MIT License](LICENSE) as the project.
