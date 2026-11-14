#!/bin/bash
# Inspect installed code and service layout without invoking the boot installer.
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
if (( $# )); then
    echo 'Usage: fuxi-device-baseline.sh' >&2
    exit 2
fi
mkdir -p "$B/work/device-baseline"
log="$B/work/device-baseline/$(date +%Y%m%d-%H%M%S).json"
digest="$(sha256sum "$B/packaging/fuxi-device/root/usr/sbin/fuxi-kernel-install")"
digest="${digest%% *}"
timeout -k 2s 40s "$B/scripts/fuxi-ssh.sh" "python3 - --expected-installer-sha256 $digest" \
    < "$B/scripts/fuxi-device-baseline.py" | tee "$log"
echo "Saved: $log" >&2
