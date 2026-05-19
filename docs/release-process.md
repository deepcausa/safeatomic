# Release process

This document is the canonical procedure for cutting and publishing a
new `safeatomic` release. It is written for maintainers, but anyone
reading it should be able to reproduce a release end-to-end.

If you only want to *propose* changes for the next release, see
[`CONTRIBUTING.md`](../CONTRIBUTING.md) instead. Just adding an entry
under `## [Unreleased]` in `CHANGELOG.md` in your PR is enough.

## Versioning policy

`safeatomic` follows semantic versioning, with one project-specific
amendment: **weakening any cell of the guarantee matrix is a major
version bump**, even if the function signature does not change. See
ADR-0006 (in the private `safeatomic-project/adr/` corpus).

- **Patch** (`2.0.X`): bug fixes, documentation, packaging, CI,
  examples, dependency bumps. No public-surface change.
- **Minor** (`2.X.0`): new public names (adds to `__all__`,
  with matching `_EXPECTED_PUBLIC_NAMES` bump), new optional kwargs,
  strengthened guarantees, new formats. Existing callers continue to
  work unchanged.
- **Major** (`X.0.0`): any breaking change to the 43-name surface,
  any weakened guarantee, dropping a supported Python version, or
  dropping a supported filesystem tier.

## Pre-flight: what must already be true

Before opening a release PR, make sure all of these hold on `main`:

- All tests green on Python 3.12 and 3.13 (CI badge green).
- TLA+ formal-model workflow green (`Formal models` badge green).
- Codecov upload from the latest CI run completed without error.
- No open `dependabot` PRs that you intend to ship in this release.
- The `## [Unreleased]` section of `CHANGELOG.md` reflects everything
  merged since the last tag.
- No uncommitted local changes; working tree clean.

## Cutting the release

### 1. Bump the version

Edit `pyproject.toml`:

```toml
[project]
version = "X.Y.Z"
```

There is **no** `__version__` constant to edit in the source tree.
`src/safeatomic/__init__.py` reads it from
`importlib.metadata.version("safeatomic")` at import time, with a
`PackageNotFoundError` fallback to `"0.0.0+unknown"` so the package
remains importable when run from an uninstalled checkout. This was
fixed in 2.0.3 — earlier versions reported a stale string.

### 2. Update the changelog

In `CHANGELOG.md`:

1. Rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`.
2. Create a fresh empty `## [Unreleased]` section above it with the
   placeholder `*(no changes yet)*`.
3. Update the footer reference links so that:
   - `[Unreleased]` compares the new tag to `HEAD`,
   - `[X.Y.Z]` points at the release tag URL.

The release section must include, in order:

- `### Fixed` (if any)
- `### Added` (if any)
- `### Changed` (if any)
- `### Guarantees` — even if just `No change to the guarantee
  matrix.` This pins the contract explicitly for every release.

### 3. Open the release PR

```bash
git checkout -b release/vX.Y.Z
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): vX.Y.Z"
git push -u origin release/vX.Y.Z
```

Open the PR against `main`. The PR title and body become the squash
commit (the only enabled merge method on this repo). Title:
`chore(release): vX.Y.Z`. Body should include:

- one-paragraph summary of the release theme;
- a Verification subsection listing what was checked locally
  (ruff, mypy, pytest, build, twine, smoke import);
- a "Public surface" line confirming `len(__all__) == 43` (or
  the new number with the matching `_EXPECTED_PUBLIC_NAMES` change);
- a "Guarantees" line confirming the matrix is unchanged (or naming
  the cell that moved and linking the ADR).

### 4. Wait for CI

Three status checks must pass:

- `Lint + type-check`
- `Tests (Python 3.12)`
- `Tests (Python 3.13)`

If any fail, fix on the same branch. Do not merge red.

### 5. Squash-merge

Squash-merge through the GitHub UI or the API. The repo's branch
protection makes squash the only enabled merge method; rebase and
merge commits are disabled.

### 6. Create the GitHub Release

In the GitHub UI: **Releases → Draft a new release**.

- **Tag**: `vX.Y.Z`, target the post-merge `main` HEAD.
- **Title**: `vX.Y.Z`.
- **Body**: copy the `## [X.Y.Z]` section from `CHANGELOG.md`,
  optionally with a one-paragraph human-friendly summary at the top
  and a link back to the changelog section.
- **Set as the latest release**: yes.
- Click **Publish release**. This creates the tag.

The `publish.yml` workflow triggers on `release: published`. It does
**not** trigger on a bare tag push — by design, so a human always
sits in the loop between merge and PyPI.

### 7. Approve the PyPI deployment

The `pypi` environment has both a required-reviewer rule and a
deployment-branch policy that allows only `main` plus tags matching
`v*`. When the publish workflow reaches the publish job, the run
will sit in **status: waiting**.

1. Open the run from the **Actions** tab.
2. Find the `publish` job, click **Review pending deployments**.
3. Approve the `pypi` environment deploy.

The job will then upload via `pypa/gh-action-pypi-publish` to PyPI
using the `PYPI_API_TOKEN` Actions secret. The publish step is
idempotent only on first success — re-uploading the same version
will fail because PyPI does not allow overwrites.

### 8. Smoke-test the published artefact

```bash
python3 -m venv /tmp/smoke && \
  source /tmp/smoke/bin/activate && \
  pip install --no-cache-dir safeatomic==X.Y.Z && \
  python -c "
import safeatomic
from importlib.metadata import version
assert safeatomic.__version__ == version('safeatomic') == 'X.Y.Z'
assert len(safeatomic.__all__) == 43
from safeatomic import write_atomic, read_atomic
write_atomic('/tmp/smoke.txt', f'hello vX.Y.Z')
assert read_atomic('/tmp/smoke.txt', encoding='utf-8') == 'hello vX.Y.Z'
print('OK')
"
```

CDN propagation can take 1–5 minutes after the publish job
completes. Use `--no-cache-dir` to bypass pip's local index cache.

## What the build verifies

The publish workflow's verify step asserts the following before
upload — in Python, with no shell pipes that could swallow errors:

- sdist contains every file under `formal/` (3 `.tla`),
  `examples/README.md`, two representative example scripts,
  `docs/formal-models.md`, and `scripts/check-formal.sh`;
- wheel contains **none** of `formal/`, `examples/`, `tests/`, or
  `scripts/`.

If you change the sdist/wheel layout, update the verify step too.

## Rolling back

PyPI does not allow overwriting a published version, but you can:

- **Yank** a release with `pypi → manage → yank`. Existing installs
  keep working; new resolutions skip the yanked version. Use this
  for shipping-bug releases.
- **Publish a fix release.** This is almost always preferable.
  v2.0.1 was an example: a workflow-verification bug prevented PyPI
  upload, the tag and GitHub Release were deleted (because nothing
  had been published), and the fixes landed in v2.0.2. v2.0.2 itself
  shipped with a `__version__` drift, which was fixed in v2.0.3
  without yanking 2.0.2 because the drift was cosmetic.

If a release introduces a guarantee weakening that was not caught in
review, yank immediately and open a major version PR. Do not patch
silently.

## Related procedures

- [`AGENTS.md`](../AGENTS.md) — the hard invariants and don'ts.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — the contributor workflow
  for non-release changes.
- [`docs/development.md`](development.md) — how to reproduce the CI
  jobs (coverage, TLA+) locally before submitting.

## Family of repos

`safeatomic` is part of three related repositories. The other two
have their own release procedures, not documented here:

- [`safeatomic-rs`](https://github.com/deepcausa/safeatomic-rs) —
  publishes to crates.io via `cargo publish` rather than PyPI.
- [`datawal`](https://github.com/deepcausa/datawal) — same
  toolchain; depends on `safeatomic-rs` at the crate level.

Releases across the family are coordinated informally. There is no
shared version number.
