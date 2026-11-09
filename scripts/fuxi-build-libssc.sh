#!/bin/bash
# Build an isolated candidate; never install into the host or device.
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
S="$B/build/sources/libssc"
O="$B/work/deps/libssc"
python3 "$B/scripts/fuxi-sources.py" prepare libssc
mkdir -p "$O"
meson setup --wipe "$O/build" "$S" --buildtype=release --prefix=/usr
ninja -C "$O/build" src/libssc.so.2
cc -std=c99 -Wall -Wextra -Werror -O2 -I "$S/src" \
    "$B/scripts/tests/test_fuxi_libssc_matrix.c" -lm -o "$O/matrix-test"
"$O/matrix-test"
cp "$S/.fuxi-source.json" "$O/source.json"
cp /build-environment.lock "$O/build-environment.lock"
sha256sum "$O/build/src/libssc.so.2" "$O/source.json" "$O/build-environment.lock" > "$O/SHA256SUMS"
printf 'Isolated candidate: %s\n' "$O/build/src/libssc.so.2"
