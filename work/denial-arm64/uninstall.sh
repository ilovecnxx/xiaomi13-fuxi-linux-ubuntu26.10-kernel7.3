#!/bin/bash
# Remove the device-local ARM64 Denial packages. Phosh/kernel/session untouched.
set -euo pipefail
[[ "$(id -u)" -eq 0 ]] || { echo "uninstall.sh: must run as root" >&2; exit 1; }
apt-get remove -y denial denial-flutter-engine 2>/dev/null || dpkg -r denial denial-flutter-engine
echo "removed. /etc/denial remaining config (conffiles) left in place; use --purge to drop it:"
echo "  apt-get purge -y denial denial-flutter-engine"
