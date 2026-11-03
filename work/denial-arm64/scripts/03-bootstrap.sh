#!/bin/bash
set -x
source /home/fuxi/denial-logs/env.sh
date -Is
mkdir -p /home/fuxi/denial-cache/pc-build /home/fuxi/denial-cache/flutter-engine /home/fuxi/denial-cache/pc-dependencies
if [[ ! -d /home/fuxi/denial-src/.git ]]; then
  git clone --depth 1 --branch v0.5.1 https://github.com/denialwm/denial.git /home/fuxi/denial-src || { echo CLONE_FAILED; exit 1; }
fi
cd /home/fuxi/denial-src || exit 1
git log -1 --format='HEAD=%H %d %s'
git tag --points-at HEAD
git status --porcelain | head
echo "=== bootstrap ==="
tools/denial-pc bootstrap
rc=$?
echo "bootstrap_rc=$rc"
date -Is
df -h / | tail -1
du -sh /home/fuxi/denial-cache/* 2>/dev/null
exit $rc
