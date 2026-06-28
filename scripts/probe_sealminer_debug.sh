#!/usr/bin/env bash
#
# Ad-hoc Sealminer/bdminer API probe for field debugging (shell version).
#
# Usage:
#   ./scripts/probe_sealminer_debug.sh <miner-ip> [port]
#
# Connects to the CGMiner-style TCP API and prints the raw response to each
# command. Tells you definitively whether:
#   * port 4028 is reachable from this host (API network access / firewall)
#   * the device responds to {"command":"version"} and with what keys
#   * whether it fingerprints as Sealminer (a "bdminer"/"Bdminer" marker)
#
# Portable: uses `nc` if available, otherwise falls back to bash /dev/tcp.
# On Windows, run from Git Bash or WSL. Needs `timeout` (coreutils) for the
# /dev/tcp fallback; `nc` already handles its own timeout via -w.

set -u

IP="${1:-}"
PORT="${2:-4028}"
TIMEOUT="${TIMEOUT:-5}"   # seconds; override with TIMEOUT=10 ./probe...

if [[ -z "$IP" ]]; then
  grep '^#' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

# Pick a pretty-printer if one is around (purely cosmetic).
pretty() {
  if command -v jq >/dev/null 2>&1; then
    jq . 2>/dev/null || cat
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import sys,json;
try:
    print(json.dumps(json.load(sys.stdin), indent=2))
except Exception:
    pass' 2>/dev/null || cat
  else
    cat
  fi
}

# Send one command and echo the raw reply. bdminer may leave the socket open,
# so both paths rely on a timeout rather than waiting for EOF.
send_cmd() {
  local cmd="$1"
  local payload="{\"command\":\"$cmd\"}"
  if command -v nc >/dev/null 2>&1; then
    printf '%s' "$payload" | nc -w "$TIMEOUT" "$IP" "$PORT" 2>/dev/null
  else
    # bash /dev/tcp fallback
    if ! exec 3<>"/dev/tcp/$IP/$PORT" 2>/dev/null; then
      return 1
    fi
    printf '%s' "$payload" >&3
    if command -v timeout >/dev/null 2>&1; then
      timeout "$TIMEOUT" cat <&3
    else
      cat <&3 &   # no timeout available: best-effort, may block until close
      local pid=$!
      ( sleep "$TIMEOUT"; kill "$pid" 2>/dev/null ) >/dev/null 2>&1 &
      wait "$pid" 2>/dev/null
    fi
    exec 3<&- 3>&-
  fi
}

echo "Probing $IP:$PORT (timeout ${TIMEOUT}s) ..."

# First, a reachability check on the version command.
ver_raw="$(send_cmd version)"
if [[ -z "$ver_raw" ]]; then
  echo
  echo "  [no response] Nothing came back from $IP:$PORT."
  echo "  -> Port likely closed/filtered: firewall, wrong subnet, or API off."
  echo "     (Sealminer API is read-only and on by default, so this is a"
  echo "      network-reachability problem, not authentication.)"
  exit 2
fi

if printf '%s' "$ver_raw" | grep -qi 'bdminer'; then
  echo "  [MATCH] Fingerprints as Sealminer (bdminer marker present)."
else
  echo "  [no match] Answered on $PORT but no 'bdminer' marker —"
  echo "            not a Sealminer, or a firmware variant. See raw reply below."
fi

for cmd in version summary stats devdetails; do
  echo
  echo "=== command: $cmd ==="
  raw="$(send_cmd "$cmd")"
  if [[ -z "$raw" ]]; then
    echo "  [no response]"
    continue
  fi
  printf '%s\n' "$raw" | pretty
done
