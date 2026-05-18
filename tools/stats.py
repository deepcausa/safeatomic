#!/usr/bin/env python3
"""
Stats for safeatomic — Python/uv project overview.

Sections:
  1. Lines of code by language (Python, Markdown, TOML, YAML, ...)
  2. Breakdown by top-level dir (src, tests, docs, tools)
  3. Git activity
  4. Summary

Fixtures (tests/fixtures/) are excluded by default — they are synthetic
recordings, not code. Pass --with-fixtures to include them.
"""

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ANSI
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"

EXT_TO_LANG = {
    ".py": "Python",
    ".pyi": "Python",
    ".md": "Markdown",
    ".toml": "TOML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".sh": "Shell",
    ".bash": "Shell",
    ".j2": "Jinja",
    "Dockerfile": "Dockerfile",
}

# Exclude from LOC entirely
SKIP_EXTS = {
    ".csv", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".pdf", ".lock", ".zip", ".tar", ".gz",
    ".p12", ".crt", ".key", ".pem", ".bin",
}

# Files always skipped (lock files, etc) — these are generated artifacts
SKIP_NAMES = {"uv.lock", "poetry.lock", "package-lock.json", "Cargo.lock"}

SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "target",
    "dist",
    "build",
    ".tox",
}

# Test fixtures: synthetic recordings, not source. Excluded by default.
FIXTURE_DIRS = {"fixtures", "recordings", "cassettes"}


def count_lines(path: Path) -> int:
    try:
        return path.read_text(errors="replace").count("\n")
    except OSError:
        return 0


def classify(path: Path) -> str | None:
    name = path.name
    if name in SKIP_NAMES:
        return None
    if name in EXT_TO_LANG:
        return EXT_TO_LANG[name]
    ext = path.suffix.lower()
    if ext in SKIP_EXTS:
        return None
    return EXT_TO_LANG.get(ext)  # None if untracked extension


def is_fixture(path: Path, root: Path) -> bool:
    """True if file lives under a tests/fixtures-like dir."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return any(part in FIXTURE_DIRS for part in rel.parts)


def iter_files(root: Path, base: Path, include_fixtures: bool):
    for f in base.rglob("*"):
        if not f.is_file():
            continue
        rel_parts = set(f.relative_to(root).parts)
        if rel_parts & SKIP_DIRS:
            continue
        if not include_fixtures and is_fixture(f, root):
            continue
        yield f


def by_language(root: Path, include_fixtures: bool) -> dict[str, tuple[int, int]]:
    counts: dict[str, list[int]] = {}
    for f in iter_files(root, root, include_fixtures):
        lang = classify(f)
        if lang is None:
            continue
        counts.setdefault(lang, []).append(count_lines(f))
    return {lang: (len(vals), sum(vals)) for lang, vals in counts.items()}


def by_dir(root: Path, include_fixtures: bool) -> list[tuple[str, int, int]]:
    out = []
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        if p.name in SKIP_DIRS:
            continue
        files, lines = 0, 0
        for f in iter_files(root, p, include_fixtures):
            if classify(f) is None:
                continue
            files += 1
            lines += count_lines(f)
        if files:
            out.append((p.name, files, lines))
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def git_stats(root: Path) -> dict | None:
    try:
        r = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=5,
        )
        commits = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0

        r2 = subprocess.run(
            ["git", "log", "-1", "--format=%ci"],
            cwd=root, capture_output=True, text=True, timeout=5,
        )
        last = (
            r2.stdout.strip()[:10] if r2.returncode == 0 and r2.stdout.strip() else "?"
        )

        r3 = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=5,
        )
        branch = r3.stdout.strip() if r3.returncode == 0 else "?"

        return {"commits": commits, "last_commit": last, "branch": branch}
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None


def fmt_num(n: int) -> str:
    return f"{n:,}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--with-fixtures",
        action="store_true",
        help="Include tests/fixtures/* in the counts (default: excluded)",
    )
    args = ap.parse_args()

    root = Path(os.environ.get("SAFEATOMIC_ROOT", ROOT)).resolve()
    repo_name = root.name
    fixtures_note = "" if args.with_fixtures else f"  {DIM}(fixtures excluded){RESET}"

    print()
    print(f"{BOLD}{'═' * 56}{RESET}")
    print(f"{BOLD}  {repo_name} stats{RESET}  {DIM}{root}{RESET}{fixtures_note}")
    print(f"{BOLD}{'═' * 56}{RESET}")

    # ── By language ─────────────────────────────────────────────────────────
    print(f"\n{BOLD}{CYAN}  LINES BY LANGUAGE{RESET}")
    print(f"  {DIM}{'─' * 52}{RESET}")

    by_lang = by_language(root, args.with_fixtures)
    if not by_lang:
        print("  (no files)")
    else:
        rows = sorted(by_lang.items(), key=lambda x: x[1][1], reverse=True)
        max_lines = rows[0][1][1] if rows else 1
        for lang, (files, lines) in rows:
            if lines == 0:
                continue
            bar_w = max(0, min(36, int(lines / max_lines * 36)))
            bar = f"{GREEN}{'█' * bar_w}{DIM}{'░' * (36 - bar_w)}{RESET}"
            print(
                f"  {lang:<14} {BOLD}{fmt_num(lines):>8}{RESET}  {files:>4} files  {bar}"
            )
        total_files = sum(v[0] for v in by_lang.values())
        total_lines = sum(v[1] for v in by_lang.values())
        print(f"  {DIM}{'─' * 52}{RESET}")
        print(
            f"  {BOLD}{'TOTAL':<14}{RESET} {BOLD}{GREEN}{fmt_num(total_lines):>8}{RESET}  {total_files:>4} files"
        )

    # ── By directory ────────────────────────────────────────────────────────
    dir_rows = by_dir(root, args.with_fixtures)
    if dir_rows:
        print(f"\n{BOLD}{CYAN}  BY DIRECTORY{RESET}")
        print(f"  {DIM}{'─' * 52}{RESET}")
        max_d = max(r[2] for r in dir_rows) or 1
        for name, files, lines in dir_rows:
            if lines == 0:
                continue
            bar_w = max(0, min(36, int(lines / max_d * 36)))
            bar = f"{CYAN}{'█' * bar_w}{DIM}{'░' * (36 - bar_w)}{RESET}"
            print(
                f"  {name:<14} {BOLD}{fmt_num(lines):>8}{RESET}  {files:>4} files  {bar}"
            )
        print(f"  {DIM}{'─' * 52}{RESET}")

    # ── Git ──────────────────────────────────────────────────────────────────
    print(f"\n{BOLD}{CYAN}  GIT{RESET}")
    print(f"  {DIM}{'─' * 52}{RESET}")
    gs = git_stats(root)
    if gs:
        print(f"  {DIM}{'Branch':<20}{RESET}  {BOLD}{gs['branch']}{RESET}")
        print(f"  {DIM}{'Commits':<20}{RESET}  {BOLD}{fmt_num(gs['commits'])}{RESET}")
        print(f"  {DIM}{'Last commit':<20}{RESET}  {BOLD}{gs['last_commit']}{RESET}")
    else:
        print("  (git not available)")

    # ── Summary ─────────────────────────────────────────────────────────────
    py = by_lang.get("Python", (0, 0))
    md = by_lang.get("Markdown", (0, 0))
    toml = by_lang.get("TOML", (0, 0))
    yaml = by_lang.get("YAML", (0, 0))
    json_ = by_lang.get("JSON", (0, 0))

    # Split src/ vs tests/
    src_dir = root / "src"
    tests_dir = root / "tests"
    src_py_files = src_py_lines = 0
    tests_py_files = tests_py_lines = 0
    if src_dir.is_dir():
        for f in iter_files(root, src_dir, args.with_fixtures):
            if f.suffix == ".py":
                src_py_files += 1
                src_py_lines += count_lines(f)
    if tests_dir.is_dir():
        for f in iter_files(root, tests_dir, args.with_fixtures):
            if f.suffix == ".py":
                tests_py_files += 1
                tests_py_lines += count_lines(f)

    print(f"\n{BOLD}{CYAN}  SUMMARY{RESET}")
    print(f"  {DIM}{'─' * 52}{RESET}")
    for label, (files, lines) in [
        ("Python (total)", py),
        ("  src/", (src_py_files, src_py_lines)),
        ("  tests/", (tests_py_files, tests_py_lines)),
        ("Markdown", md),
        ("TOML", toml),
        ("YAML", yaml),
        ("JSON", json_),
    ]:
        print(
            f"  {DIM}{label:<24}{RESET}  {BOLD}{files:>4}{RESET} files  "
            f"{BOLD}{fmt_num(lines):>8}{RESET} lines"
        )

    if src_py_lines and tests_py_lines:
        ratio = tests_py_lines / src_py_lines
        print(f"\n  {DIM}{'Test/src ratio':<24}{RESET}  {BOLD}{ratio:.2f}x{RESET}")

    print()
    print(f"{BOLD}{'═' * 56}{RESET}\n")


if __name__ == "__main__":
    main()
