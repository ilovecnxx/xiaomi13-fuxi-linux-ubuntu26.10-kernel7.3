#!/bin/bash
# Phase 2: build the pinned Flutter engine natively for linux-arm64 (release)
set -x
source /home/fuxi/denial-logs/env.sh
cd /home/fuxi/denial-src || exit 1
date -Is
echo "=== prepare-app-build (engine, native aarch64) ==="
tools/denial-flutter-engine prepare-app-build
rc=$?
echo "engine_rc=$rc"
date -Is
df -h / | tail -1
du -sh /home/fuxi/denial-cache/flutter-engine 2>/dev/null
ls -l /home/fuxi/denial-cache/flutter-engine/build/out/denial_host_release_arm64/libflutter_engine.so 2>/dev/null
file /home/fuxi/denial-cache/flutter-engine/build/out/denial_host_release_arm64/libflutter_engine.so 2>/dev/null
exit $rc
