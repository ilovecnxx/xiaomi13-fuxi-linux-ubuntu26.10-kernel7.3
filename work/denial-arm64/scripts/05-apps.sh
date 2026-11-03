#!/bin/bash
# Phase 3: shell bundle, Settings app, Rust compositor/denialctl/portal
set -x
source /home/fuxi/denial-logs/env.sh
cd /home/fuxi/denial-src || exit 1
date -Is
echo "=== bundle (dart_shell) ==="
tools/denial-pc bundle
echo "bundle_rc=$?"
echo "=== settings ==="
tools/denial-pc settings
echo "settings_rc=$?"
echo "=== compositor ==="
tools/denial-pc compositor
echo "compositor_rc=$?"
date -Is
df -h / | tail -1
file /home/fuxi/denial-cache/pc-build/rust/release/deniald \
     /home/fuxi/denial-cache/pc-build/rust/release/denialctl \
     /home/fuxi/denial-cache/pc-build/rust/release/denial-portal \
     /home/fuxi/denial-src/dart_shell/build/linux/arm64/release/bundle/lib/libapp.so \
     /home/fuxi/denial-src/settings_app/build/linux/arm64/release/bundle/lib/libapp.so 2>&1
