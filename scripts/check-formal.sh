#!/usr/bin/env sh
# Run TLC against each .tla model in formal/ and report results.
#
# Exit codes:
#   0  every model reported "No error has been found"
#   1  one or more models failed (output preserved under formal/reports/)
#   2  TLC not found (neither ~/.local/bin/tlc nor TLC_JAR set)
#   3  invoked from the wrong directory
#
# Flags:
#   --update-reports   overwrite the committed report files under formal/reports/
#                      (default: do not touch them; write to a temp dir instead)
#
# Environment:
#   TLC_JAR    Path to tla2tools.jar. If set, takes precedence over the wrapper.
#              SHA-256 should match the value pinned in formal/README.md.
#
# This script is intentionally POSIX sh, not bash. Tested with dash and bash.

set -eu

# ---------------------------------------------------------------------------
# Locate the repo root (the directory containing formal/).
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ ! -d "$REPO_ROOT/formal" ]; then
    echo "error: formal/ directory not found under $REPO_ROOT" >&2
    echo "       run this script from anywhere inside the safeatomic repo" >&2
    exit 3
fi

FORMAL_DIR="$REPO_ROOT/formal"
REPORTS_DIR="$FORMAL_DIR/reports"

# ---------------------------------------------------------------------------
# Locate TLC.
# ---------------------------------------------------------------------------

if [ -n "${TLC_JAR:-}" ]; then
    if [ ! -f "$TLC_JAR" ]; then
        echo "error: TLC_JAR=$TLC_JAR does not exist" >&2
        exit 2
    fi
    TLC_CMD="java -cp $TLC_JAR tlc2.TLC"
elif [ -x "$HOME/.local/bin/tlc" ]; then
    TLC_CMD="$HOME/.local/bin/tlc"
else
    echo "error: no TLC found." >&2
    echo "       Either set TLC_JAR=/path/to/tla2tools.jar," >&2
    echo "       or install the wrapper at ~/.local/bin/tlc" >&2
    echo "       (see formal/README.md for instructions)." >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Parse flags.
# ---------------------------------------------------------------------------

UPDATE_REPORTS=0
for arg in "$@"; do
    case "$arg" in
        --update-reports) UPDATE_REPORTS=1 ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "error: unknown flag: $arg" >&2
            echo "       (try --help)" >&2
            exit 3
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Decide where the fresh reports go.
# ---------------------------------------------------------------------------

if [ "$UPDATE_REPORTS" -eq 1 ]; then
    OUT_DIR="$REPORTS_DIR"
    mkdir -p "$OUT_DIR"
else
    OUT_DIR="$(mktemp -d -t safeatomic-formal-XXXXXX)"
fi

RUN_DATE="$(date -u +%Y-%m-%d)"

# ---------------------------------------------------------------------------
# Run the three models.
# ---------------------------------------------------------------------------

MODELS="SafeAtomicSmoke SafeAtomicLock SafeAtomicChecksum"
FAIL_COUNT=0

cd "$FORMAL_DIR"

for model in $MODELS; do
    spec="$FORMAL_DIR/$model.tla"
    report="$OUT_DIR/${RUN_DATE}-safeatomic-$(echo "$model" | tr '[:upper:]' '[:lower:]' | sed 's/safeatomic//').txt"
    # The naming above produces e.g. 2026-05-19-safeatomic-smoke.txt
    if [ ! -f "$spec" ]; then
        echo "error: $spec not found" >&2
        FAIL_COUNT=$((FAIL_COUNT + 1))
        continue
    fi

    printf '%-22s ' "$model"
    # shellcheck disable=SC2086   # TLC_CMD may be multiple tokens (java -cp ...)
    if $TLC_CMD "$spec" > "$report" 2>&1; then
        if grep -q "No error has been found" "$report"; then
            states=$(grep -E "states generated" "$report" | head -1)
            depth=$(grep -E "depth of the complete" "$report" | head -1)
            printf 'PASS  %s  %s\n' "$states" "$depth"
        else
            printf 'FAIL (TLC exited 0 but no "No error has been found" in stdout)\n'
            FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    else
        rc=$?
        printf 'FAIL (TLC exit %d)\n' "$rc"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done

# ---------------------------------------------------------------------------
# Summary.
# ---------------------------------------------------------------------------

echo
if [ "$UPDATE_REPORTS" -eq 1 ]; then
    echo "reports written to: $OUT_DIR"
    echo "(committed copies updated; review with 'git diff formal/reports/')"
else
    echo "reports written to: $OUT_DIR"
    echo "(committed copies under $REPORTS_DIR were NOT modified;"
    echo " pass --update-reports to refresh them)"
fi

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "$FAIL_COUNT model(s) failed" >&2
    exit 1
fi

exit 0
