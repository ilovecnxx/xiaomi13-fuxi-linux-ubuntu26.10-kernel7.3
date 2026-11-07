#!/bin/bash
# On-demand SSC sampling; no background service or desktop changes.
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
seconds="${1:-3}"
if (( $# > 1 )) || [[ ! "$seconds" =~ ^([1-9]|10)$ ]]; then
    echo 'Usage: fuxi-gravity-sample.sh [1..10 seconds]' >&2
    exit 2
fi
mkdir -p "$B/work/gravity-sample"
log="$B/work/gravity-sample/$(date +%Y%m%d-%H%M%S).json"
timeout -k 2s 40s "$B/scripts/fuxi-ssh.sh" "timeout -k 2s 30s python3 - $seconds" \
    < "$B/packaging/fuxi-device/root/usr/libexec/fuxi/fuxi-gravity-sample" | tee "$log"
echo "Saved: $log" >&2
