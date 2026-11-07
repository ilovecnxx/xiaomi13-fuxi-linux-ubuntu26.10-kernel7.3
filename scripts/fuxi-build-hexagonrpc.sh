#!/bin/bash
# Build the pinned hexagonrpc v0.4.0 + fuxi read-only filesystem patch.
# Source and patches: packaging/sources/manifest.json.
# This script runs host tests and builds an ARM64 binary; it does not touch a phone.
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
S="$B/build/sources/hexagonrpc"
OUT="$B/work/kbuild/hexagonrpc"
REV=23a69640bf10dc498226c602c5b5db11d8cb3d8e
python3 "$B/scripts/fuxi-sources.py" check hexagonrpc
mkdir -p "$OUT"
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
mkdir -p "$W/sensors/"{config,odm-config,registry} "$W/socinfo"
printf 'vendor\n' > "$W/sensors/config/vendor.json"
printf 'odm\n' > "$W/sensors/odm-config/device.json"
touch -d @1230768000 "$W/sensors/odm-config/device.json"
printf 'cal\n' > "$W/sensors/registry/calibration"
printf 'version=14\n' > "$W/sensors/sns_reg_version"
printf '519\n' > "$W/socinfo/soc_id"
cd "$S"
gcc -O2 -Wall -Wextra -Wno-unused-parameter -Iinclude -Ihexagonrpcd \
  "$B/scripts/tests/test_hexagonfs_fuxi.c" \
  hexagonrpcd/{hexagonfs,hexagonfs_mapped,hexagonfs_virt_dir,rpcd_builder}.c \
  -o "$OUT/test-fuxi-hexagonfs"
"$OUT/test-fuxi-hexagonfs" "$W"
gcc -O2 -Iinclude tests/test_iobuffer.c hexagonrpcd/iobuffer.c -o "$OUT/test-iobuffer"
"$OUT/test-iobuffer"
gcc -O2 -Iinclude tests/test_hexagonfs.c hexagonrpcd/{hexagonfs,hexagonfs_mapped}.c -o "$OUT/test-hexagonfs"
"$OUT/test-hexagonfs" tests/sample_file.txt
aarch64-linux-gnu-gcc -O2 -static -Wall -Wextra -Wno-unused-parameter -Iinclude \
  hexagonrpcd/{aee_error,apps_mem,apps_std,interfaces,hexagonfs,hexagonfs_mapped,hexagonfs_plat_subtype_name,hexagonfs_virt_dir,iobuffer,listener,localctl,rpcd,rpcd_builder}.c \
  libhexagonrpc/{context,fastrpc,interfaces,session}.c \
  -o "$OUT/hexagonrpcd-fuxi"
cp "$S/.fuxi-source.json" "$OUT/source.json"
[ ! -f /build-environment.lock ] || cp /build-environment.lock "$OUT/build-environment.lock"
(cd "$OUT" && sha256sum hexagonrpcd-fuxi source.json > SHA256SUMS)
printf '%s\n' "$REV" > "$OUT/upstream-revision.txt"
echo "Built $OUT/hexagonrpcd-fuxi"
