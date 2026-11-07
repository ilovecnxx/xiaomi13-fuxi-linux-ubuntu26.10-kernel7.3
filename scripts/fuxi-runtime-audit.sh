#!/bin/bash
# Read-only KVM/TUN checks; no VM, network interface or routing changes.
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
if (( $# )); then
    echo 'Usage: fuxi-runtime-audit.sh' >&2
    exit 2
fi
mkdir -p "$B/work/runtime-audit"
log="$B/work/runtime-audit/$(date +%Y%m%d-%H%M%S).json"
timeout -k 2s 20s "$B/scripts/fuxi-ssh.sh" 'python3 -' \
    < "$B/packaging/fuxi-device/root/usr/libexec/fuxi/fuxi-runtime-audit" | tee "$log"
echo "Saved: $log" >&2
