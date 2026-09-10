#!/usr/bin/env bash
# Runs the telemetry agent against a LOCAL stack, in this checkout's venv.
#
# Usage: ./scripts/dev.sh [extra args passed to main.py]
#   --tui   run the Rich TUI path instead of the default Qt GUI
#
# Why this exists: wright_telemetry/settings.py defaults WRIGHT_API_URL /
# WRIGHT_INGEST_URL / WRIGHT_WS_URL to the PRODUCTION api.wrightfan.com URLs,
# and reads them from the environment only. Launching `python main.py --gui`
# by hand therefore either hits production or, if your shell still exports
# values from another checkout, quietly talks to the wrong stack — which shows
# up as "Connection refused" against ports nothing is serving.
#
# Precedence: an exported WRIGHT_* or PORT_OFFSET beats .env, which beats the
# offset-0 default. PORT_OFFSET mirrors wright-data-pipeline/scripts/dev.sh: offset 0 is the main
# checkout (portal 3001, ingest 8080, ws 8082) and each worktree owns a block
# (offset 100 -> 3101/8180/8182). new-feature.sh writes both the offset and
# explicit URLs into the worktree's .env, so `source .env` before this, or just
# let the defaults below apply in the main checkout.

set -euo pipefail
cd "$(dirname "$0")/.."

# Precedence, highest first: your shell > .env > offset-derived default.
#
# The shell has to be captured BEFORE sourcing, because .env sets these with
# `export` and would otherwise clobber a one-off override —
# `WRIGHT_API_URL=... ./scripts/dev.sh` silently did nothing in a worktree.
SHELL_API_URL="${WRIGHT_API_URL:-}"
SHELL_INGEST_URL="${WRIGHT_INGEST_URL:-}"
SHELL_WS_URL="${WRIGHT_WS_URL:-}"
SHELL_PORT_OFFSET="${PORT_OFFSET:-}"

# A worktree's .env carries its own WRIGHT_* URLs; absent in the main checkout,
# where the offset-0 defaults below are already right.
# shellcheck disable=SC1091
[ -f .env ] && . ./.env

[ -n "$SHELL_API_URL" ]    && WRIGHT_API_URL="$SHELL_API_URL"
[ -n "$SHELL_INGEST_URL" ] && WRIGHT_INGEST_URL="$SHELL_INGEST_URL"
[ -n "$SHELL_WS_URL" ]     && WRIGHT_WS_URL="$SHELL_WS_URL"
[ -n "$SHELL_PORT_OFFSET" ] && PORT_OFFSET="$SHELL_PORT_OFFSET"

PORT_OFFSET="${PORT_OFFSET:-0}"

# Validate before arithmetic: $((3001 + abc)) treats abc as a variable name and,
# under `set -u`, dies with "abc: unbound variable" — mentioning neither
# PORT_OFFSET nor .env.
case "$PORT_OFFSET" in
  ''|*[!0-9]*)
    echo "ERROR: PORT_OFFSET must be a whole number, got '$PORT_OFFSET'." >&2
    echo "       Check .env in $(pwd), or the PORT_OFFSET you exported." >&2
    exit 1 ;;
esac

export WRIGHT_API_URL="${WRIGHT_API_URL:-http://localhost:$((3001 + PORT_OFFSET))}"
export WRIGHT_INGEST_URL="${WRIGHT_INGEST_URL:-http://localhost:$((8080 + PORT_OFFSET))}"
export WRIGHT_WS_URL="${WRIGHT_WS_URL:-http://localhost:$((8082 + PORT_OFFSET))}"

if [ -x venv/bin/python ]; then
  PY=venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  echo "No venv/ here — falling back to system python3." >&2
  echo "Create one with: python3 -m venv venv && venv/bin/pip install -r requirements.txt" >&2
  PY=python3
else
  echo "No python3 found." >&2
  exit 1
fi

# Default to the Qt GUI, which despite the --gui help text is the full app
# (window + scanning engine), not a setup wizard. --tui picks the Rich path.
MODE=(--gui)
ARGS=()
for a in "$@"; do
  case "$a" in
    --tui) MODE=() ;;
    *)     ARGS+=("$a") ;;
  esac
done

echo "portal  ${WRIGHT_API_URL}"
echo "ingest  ${WRIGHT_INGEST_URL}"
echo "ws      ${WRIGHT_WS_URL}"
[ "$PORT_OFFSET" -ne 0 ] && echo "(port offset +${PORT_OFFSET})"
# Agent logs go to ~/.wright-telemetry/collector.log (RotatingFileHandler), so
# there is nothing to tee here — that file is the diagnosable record.
echo "logs    ~/.wright-telemetry/collector.log"
echo ""

exec "$PY" main.py ${MODE+"${MODE[@]}"} ${ARGS+"${ARGS[@]}"}
