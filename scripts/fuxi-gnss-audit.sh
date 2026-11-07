#!/bin/bash
# Read-only GNSS capability checks; no location session or coordinate request.
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
if (( $# )); then
    echo 'Usage: fuxi-gnss-audit.sh' >&2
    exit 2
fi
mkdir -p "$B/work/gnss-audit"
log="$B/work/gnss-audit/$(date +%Y%m%d-%H%M%S).json"
timeout -k 2s 50s "$B/scripts/fuxi-ssh.sh" 'python3 -' \
    < "$B/packaging/fuxi-device/root/usr/libexec/fuxi/fuxi-gnss-audit" | tee "$log"
echo "Saved: $log" >&2
