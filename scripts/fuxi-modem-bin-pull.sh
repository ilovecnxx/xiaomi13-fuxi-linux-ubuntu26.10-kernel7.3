#!/usr/bin/env bash
# 从手机取 fuxi-modem 包里的两个补丁版二进制（源码不进 git；补丁在 work/patches/mm-*、work/patches/81voltd-sub2-*）：
#   ModemManager 1.25.95 + 卡槽/订阅补丁   /usr/local/lib/fuxi-mm/{src/ModemManager, libmm-glib/libmm-glib.so.0*}
#   81voltd 1.2.0 + SIM2 补丁（81voltd-sub2） /usr/local/sbin/81voltd-sub2
# → work/modem-bin/（gitignore），布局同包内 /usr/libexec/fuxi/：mm/src/ModemManager（RUNPATH $ORIGIN/../libmm-glib）、mm/libmm-glib/、81voltd-sub2
# 手机已装 fuxi-modem 后，源路径换成 /usr/libexec/fuxi（脚本自动判断）。
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$B/work/modem-bin"
SSH="$B/scripts/fuxi-ssh.sh"
rm -rf "$OUT.new"; mkdir -p "$OUT.new"
if "$SSH" 'test -x /usr/libexec/fuxi/mm/src/ModemManager'; then MM=/usr/libexec/fuxi/mm V=/usr/libexec/fuxi; else MM=/usr/local/lib/fuxi-mm V=/usr/local/sbin; fi
mkdir -p "$OUT.new/mm"
"$SSH" "cd $MM && tar -cf - src/ModemManager libmm-glib/libmm-glib.so.0 libmm-glib/libmm-glib.so.0.11.0" | tar -C "$OUT.new/mm" -xf - --no-same-owner
"$SSH" "cd $V && tar -cf - 81voltd-sub2" | tar -C "$OUT.new" -xf - --no-same-owner
( cd "$OUT.new" && find . -type f | sed 's|^\./||' | LC_ALL=C sort | xargs sha256sum ) > "$OUT.sha" && mv "$OUT.sha" "$OUT.new/SHA256SUMS"
cat "$OUT.new/SHA256SUMS"
rm -rf "$OUT"; mv "$OUT.new" "$OUT"
