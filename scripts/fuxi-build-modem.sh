#!/bin/bash
# Native arm64 build inside the pinned environment; no device access.
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
[ "$(dpkg --print-architecture)" = arm64 ] || { echo 'Run in the arm64 build environment' >&2; exit 1; }
python3 "$B/scripts/fuxi-sources.py" check modemmanager 81voltd
S="$B/build/sources"
O="$B/work/kbuild/modem-source"
OUT="$B/work/modem-bin"
mkdir -p "$O"
exec 9>"$O/.lock"
flock -n 9 || exit 1
export SOURCE_DATE_EPOCH="$(git -C "$B" log -1 --format=%ct)"
export CFLAGS="-O2 -ffile-prefix-map=$B=/usr/src/fuxi"
export LDFLAGS='-Wl,--build-id=sha1'
meson setup --wipe "$O/mm" "$S/modemmanager" --buildtype=release \
  -Dbuiltin_plugins=true -Dintrospection=false -Dgtk_doc=false -Dman=false \
  -Dtests=false -Dexamples=false -Dbash_completion=false
ninja -j4 -C "$O/mm" src/ModemManager libmm-glib/libmm-glib.so.0.11.0
meson setup --wipe "$O/81voltd" "$S/81voltd" --buildtype=release
ninja -j4 -C "$O/81voltd"
T=$(mktemp -d "$B/work/.modem-bin.XXXXXX")
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/mm/"{src,libmm-glib}
install -m755 "$O/mm/src/ModemManager" "$T/mm/src/ModemManager"
install -m755 "$O/mm/libmm-glib/libmm-glib.so.0.11.0" "$T/mm/libmm-glib/"
ln -s libmm-glib.so.0.11.0 "$T/mm/libmm-glib/libmm-glib.so.0"
patchelf --set-rpath '$ORIGIN/../libmm-glib' "$T/mm/src/ModemManager"
install -m755 "$O/81voltd/81voltd" "$T/81voltd-sub2"
for name in modemmanager 81voltd; do
  cp "$S/$name/.fuxi-source.json" "$T/$name-source.json"
done
cp /build-environment.lock "$T/build-environment.lock"
(cd "$T" && find . -type f ! -name SHA256SUMS -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > SHA256SUMS)
[ ! -e "$OUT" ] || mv "$OUT" "$O/previous-$(date +%s)"
mv "$T" "$OUT"
echo "Built $OUT from verified sources"
