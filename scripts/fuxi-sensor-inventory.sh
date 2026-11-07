#!/bin/bash
# SSC discovery/attributes only; no measurement streams or service changes.
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
if (( $# )); then
    echo 'Usage: fuxi-sensor-inventory.sh' >&2
    exit 2
fi
mkdir -p "$B/work/sensor-inventory"
log="$B/work/sensor-inventory/$(date +%Y%m%d-%H%M%S).json"
timeout -k 2s 145s "$B/scripts/fuxi-ssh.sh" 'timeout -k 2s 135s python3 -' \
    < "$B/packaging/fuxi-device/root/usr/libexec/fuxi/fuxi-sensor-inventory" | tee "$log"
echo "Saved: $log" >&2
