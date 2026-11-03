#!/bin/bash
# Install the device-local ARM64 Denial build on Xiaomi 13 fuxi (Ubuntu 26.10 arm64).
#
# This script ONLY installs the packages and runs read-only checks.
# It does not start a Denial session, does not stop/disable Phosh, and does not
# touch the kernel, boot slots, or the session manager configuration.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VER=0.5.1
REL=1

[[ "$(id -u)" -eq 0 ]] || { echo "install.sh: must run as root" >&2; exit 1; }
[[ "$(dpkg --print-architecture)" == arm64 ]] || { echo "install.sh: expected arm64" >&2; exit 1; }

echo "== dependency check =="
missing=()
for p in bash coreutils dbus git init-system-helpers libc6 libegl1 libfontconfig1 libgbm1 \
         libgles2 libwayland-client0 libgcc-s1 libgl1 libinput10 libpam0g \
         libpolkit-agent-1-0 libpulse0 libseat1 libudev1 libxkbcommon0 rtkit \
         x11-xkb-utils xkb-data xwayland; do
  dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || missing+=("$p")
done
if ((${#missing[@]})); then
  echo "missing required packages: ${missing[*]}" >&2
  echo "install them first: apt-get install -y ${missing[*]}" >&2
  exit 1
fi
for p in xdg-desktop-portal xdg-desktop-portal-gtk xdg-desktop-portal-wlr zenity fonts-noto-cjk; do
  dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' \
    || echo "note: optional package not installed: $p"
done

echo "== install packages =="
dpkg -i --force-confnew \
  "$HERE/artifacts/denial-flutter-engine_${VER}-${REL}_arm64.deb" \
  "$HERE/artifacts/denial_${VER}-${REL}_arm64.deb"

echo "== installed layout =="
ls -l /usr/bin/deniald /usr/bin/denialctl /usr/bin/denial-portal /usr/bin/denial-session /usr/bin/denial-settings
file /usr/bin/deniald /usr/lib/denial/flutter/lib/libflutter_engine.so /usr/lib/denial/flutter/lib/libapp.so

echo "== read-only session check (does not start Denial) =="
denial-session --check

echo
echo "OK. Denial is installed but NOT selected: Phosh and the current session are untouched."
echo "To try Denial later, log out and choose 'Denial' in the display manager (session file:"
echo "/usr/share/wayland-sessions/denial.desktop), or run /usr/bin/denial-session from a free VT."
echo "Session default for this phone: DENIAL_SHELL_PROFILE=mobile (/etc/denial/session.conf)."
