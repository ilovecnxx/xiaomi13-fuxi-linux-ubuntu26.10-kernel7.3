#!/bin/bash
# Run a bounded LOC session, retaining aggregate statistics only.
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
seconds="${1:-30}"
if (( $# > 1 )) || [[ ! "$seconds" =~ ^([1-9]|[1-9][0-9]|1[01][0-9]|120)$ ]]; then
    echo 'Usage: fuxi-gnss-session.sh [seconds: 1..120]' >&2
    exit 2
fi
mkdir -p "$B/work/gnss-session"
log="$B/work/gnss-session/$(date +%Y%m%d-%H%M%S).json"
# Allow an in-flight call plus Stop/release/close (4 x 8s) after TERM.
timeout -k 10s "$((seconds + 145))s" "$B/scripts/fuxi-ssh.sh" \
    "timeout -s TERM -k 40s $((seconds + 90))s python3 - $seconds" \
    < "$B/packaging/fuxi-modem/root/usr/sbin/fuxi-gnss-session" | tee "$log"
echo "Saved: $log" >&2
