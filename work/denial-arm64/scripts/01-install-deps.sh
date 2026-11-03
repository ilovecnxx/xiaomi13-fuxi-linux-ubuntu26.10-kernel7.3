#!/bin/bash
set -x
export DEBIAN_FRONTEND=noninteractive
date -Is
nproc; free -m | head -2; df -h / | tail -1
apt-get update
PACKAGES="build-essential clang lld ninja-build pkg-config git curl jq unzip rsync file python3 python3-venv dpkg-dev
libdrm-dev libgbm-dev libegl-dev libgles-dev libgl-dev libinput-dev libudev-dev libxkbcommon-dev libxkbcommon-x11-dev
libfontconfig1-dev libfreetype-dev libwayland-dev wayland-protocols libseat-dev libgtk-3-dev libpam0g-dev libpulse-dev
libx11-dev libxcb1-dev libxcb-composite0-dev libxcursor-dev libxrandr-dev libxi-dev libxtst-dev
rtkit xwayland x11-xkb-utils xkb-data
libssl-dev zlib1g-dev liblzma-dev"
apt-get install -y --no-install-recommends $PACKAGES
rc=$?
echo "install_rc=$rc"
date -Is
exit $rc
