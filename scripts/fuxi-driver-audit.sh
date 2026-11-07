#!/bin/bash
# Read-only device sampling; optional FUXI_HOST selects the SSH transport.
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
seconds=${1:-10}
[[ "$seconds" =~ ^[0-9]+$ ]] && (( 10#$seconds >= 1 && 10#$seconds <= 60 )) || {
    echo 'Usage: fuxi-driver-audit.sh [1..60 seconds]' >&2
    exit 2
}
seconds=$((10#$seconds))
mkdir -p "$B/work/driver-audit"
log="$B/work/driver-audit/$(date +%Y%m%d-%H%M%S).json"
timeout -k 2s "$((seconds + 35))" "$B/scripts/fuxi-ssh.sh" "python3 - --seconds $seconds" \
    < "$B/packaging/fuxi-device/root/usr/libexec/fuxi/fuxi-driver-audit" | tee "$log"
echo "Saved: $log" >&2
