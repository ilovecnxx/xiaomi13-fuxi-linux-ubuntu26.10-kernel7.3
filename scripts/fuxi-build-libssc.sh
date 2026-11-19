#!/bin/bash
# Build an isolated candidate; never install into the host or device.
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
S="$B/build/sources/libssc"
O="${FUXI_LIBSSC_OUT:-$B/work/deps/libssc}"
python3 "$B/scripts/fuxi-sources.py" prepare libssc
mkdir -p "$O"
meson setup --wipe "$O/build" "$S" --buildtype=release --prefix=/usr
meson compile -C "$O/build"
test_log="$O/tests.log"
tests=(tests-general tests-proximity tests-light tests-accelerometer
    tests-compass tests-magnetometer tests-no-service)
if ! meson test -C "$O/build" --setup mocking --print-errorlogs 2>&1 | tee "$test_log"; then
    printf 'libssc test suite failed; see %s\n' "$test_log" >&2
    exit 1
fi
for test_name in "${tests[@]}"; do
    if ! grep -Eq "$test_name[[:space:]]+OK([[:space:]]|$)" "$test_log"; then
        printf 'libssc %s did not pass; see %s\n' "$test_name" "$test_log" >&2
        exit 1
    fi
done
if grep -Eq '(^|[[:space:]])SKIP([[:space:]]|$)' "$test_log"; then
    printf 'libssc test suite contains skipped tests; see %s\n' "$test_log" >&2
    exit 1
fi
cc -std=c99 -Wall -Wextra -Werror -O2 -I "$S/src" \
    "$B/scripts/tests/test_fuxi_libssc_matrix.c" -lm -o "$O/matrix-test"
"$O/matrix-test"
cp "$S/.fuxi-source.json" "$O/source.json"
cp /build-environment.lock "$O/build-environment.lock"
(cd "$O" && sha256sum build/src/libssc.so.2 source.json build-environment.lock > SHA256SUMS)
(cd "$O" && sha256sum --check SHA256SUMS)
printf 'Isolated candidate: %s\n' "$O/build/src/libssc.so.2"
